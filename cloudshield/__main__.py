import argparse
import json
import sys
from functools import partial
from pathlib import Path

from .candidate import prepare_candidate, prepare_clean_lab, run_candidate
from .checks import check, check_python, compare, read_object, summarize_checks, validate_config
from .ci import run_ci
from .controller import repair
from .evidence import collect_evidence, snapshot_config
from .faults import CATALOG, fault_catalog, run_fault
from .fixer import DEFAULT_MODEL, propose_gemini
from .judge import judge_report
from .monitor import monitor
from .performance import PROFILES, run_load
from .performance_gate import REPAIR_PROFILES, make_performance_judge
from .plant import plant
from .publisher import publish
from .reviewer import DEFAULT_REVIEW_MODEL, review_gemini
from .verifier import verify
from .watcher import watch


def main():
    parser = argparse.ArgumentParser(prog="cloudshield", description="CloudShield learning labs")
    commands = parser.add_subparsers(dest="command", required=True)
    dashboard = commands.add_parser("serve", help="Open the local dashboard and persistent background worker")
    dashboard.add_argument("--port", type=int, default=18700)
    dashboard.add_argument("--database", type=Path)
    dashboard.add_argument("--no-worker", action="store_true")
    background = commands.add_parser("worker", help="Run the durable dashboard job worker")
    background.add_argument("--database", type=Path)
    background.add_argument("--once", action="store_true")
    cloud = commands.add_parser("aws", help="Enroll, observe and recover a registered ECS deployment")
    cloud_actions = cloud.add_subparsers(dest="action", required=True)
    enrollment = cloud_actions.add_parser("enroll")
    enrollment.add_argument("--stack", required=True)
    enrollment.add_argument("--profile", required=True)
    enrollment.add_argument("--region", required=True)
    enrollment.add_argument("--output", type=Path, required=True)
    for action in ("observe", "recover", "fault"):
        cloud_actions.add_parser(action).add_argument("manifest", type=Path)
    diff = commands.add_parser("compare", help="Compare two JSON environment snapshots")
    diff.add_argument("reference", type=Path)
    diff.add_argument("candidate", type=Path)
    diff.add_argument("--allow-difference", action="append", default=[])
    static = commands.add_parser("static", help="Check Python syntax and config without starting the app")
    static.add_argument("--source", type=Path, required=True)
    static.add_argument("--config", type=Path, required=True)
    static.add_argument("--python-version", help="Match the target's Python major.minor version")
    judge = commands.add_parser("judge", help="Run static checks and the required Robot suite")
    judge.add_argument("manifest", type=Path)
    judge.add_argument("--report", type=Path)
    evidence = commands.add_parser("evidence", help="Collect a local Compose project's runtime evidence")
    snapshot = commands.add_parser("snapshot", help="Read safe settings from a running backend")
    for command in (evidence, snapshot):
        command.add_argument("--compose", type=Path, required=True)
        command.add_argument("--env-file", type=Path, required=True)
        command.add_argument("--project", default="cloudshield-phase1")
        command.add_argument("--report", type=Path)
    evidence.add_argument("--log-lines", type=int, default=0)
    candidate = commands.add_parser("candidate", help="Prepare or run an isolated Phase 7 candidate")
    actions = candidate.add_subparsers(dest="action", required=True)
    prepare = actions.add_parser("prepare", help="Apply a known patch in a separate Git worktree")
    prepare.add_argument("manifest", type=Path)
    prepare.add_argument("patch", type=Path)
    prepare.add_argument("--allow-path", action="append", required=True)
    prepare.add_argument("--port", type=int, default=18002)
    clean_lab = actions.add_parser("lab", help="Prepare a clean disposable managed deployment")
    clean_lab.add_argument("manifest", type=Path)
    clean_lab.add_argument("--port", type=int, default=18012)
    for action in ("start", "stop", "discard"):
        actions.add_parser(action).add_argument("project")
    verifier = commands.add_parser("verify", help="Compare the baseline and candidate Judge reports by check ID")
    verifier.add_argument("baseline", type=Path)
    verifier.add_argument("candidate_report", type=Path)
    verifier.add_argument("--candidate-record", type=Path, required=True)
    verifier.add_argument("--target-check", action="append", default=[])
    verifier.add_argument("--report", type=Path)
    controller = commands.add_parser("repair", help="Judge a failing deployment and try patches in isolation")
    controller.add_argument("manifest", type=Path)
    patch_source = controller.add_mutually_exclusive_group(required=True)
    patch_source.add_argument("--patch", type=Path, action="append")
    patch_source.add_argument("--gemini", action="store_true", help="Send approved source and check evidence to Gemini")
    controller.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model ID (used with --gemini)")
    controller.add_argument("--review", action="store_true", help="Require a separate Gemini review before building")
    controller.add_argument("--review-model", default=DEFAULT_REVIEW_MODEL, help="Gemini model ID (used with --review)")
    controller.add_argument("--allow-path", action="append", required=True)
    controller.add_argument("--port", type=int, default=18002)
    controller.add_argument("--max-attempts", type=int, default=2)
    publisher = commands.add_parser("publish", help="Create a local branch for an exact verified repair")
    publisher.add_argument("repair_report", type=Path)
    publisher.add_argument("--repo", type=Path, required=True, help="Target Git checkout")
    publisher.add_argument("--base-ref", required=True, help="Full Git ref expected to point to the failing revision")
    watcher = commands.add_parser("watch", help="Watch local Python saves and test a settled snapshot")
    watcher.add_argument("manifest", type=Path)
    watcher.add_argument("--once", action="store_true", help="Check one settled snapshot and exit")
    watcher.add_argument("--debounce", type=float, default=1.0, help="Seconds without changes before testing")
    watcher.add_argument("--runtime", action="store_true", help="Run Robot checks in an isolated Docker environment")
    watcher.add_argument("--port", type=int, default=18004, help="Port for the isolated snapshot")
    watcher.add_argument("--gemini", action="store_true", help="Attempt one reviewed repair after a runtime failure")
    watcher.add_argument("--allow-path", action="append", default=[])
    watcher.add_argument("--repair-port", type=int, default=18005, help="Port for the AI repair candidate")
    ci = commands.add_parser("ci", help="Check an exact FastAPI Git revision in a disposable environment")
    ci.add_argument("--repo", type=Path, required=True)
    ci.add_argument("--revision", required=True, help="Full SHA from the GitHub event")
    ci.add_argument("--gemini", action="store_true", help="Attempt a reviewed repair for supported failures")
    ci.add_argument("--allow-path", action="append", default=[])
    scheduled = commands.add_parser("monitor", help="Check a running Compose deployment on a timer")
    scheduled.add_argument("manifest", type=Path)
    scheduled.add_argument("--once", action="store_true", help="Observe the deployment once and exit")
    scheduled.add_argument("--interval", type=float, help="Wait between checks (30 seconds, or 300 with JMeter)")
    scheduled.add_argument("--gemini", action="store_true", help="Attempt reviewed repair for a supported incident")
    scheduled.add_argument("--allow-path", action="append", default=[])
    scheduled.add_argument("--port", type=int, default=18006, help="Port for the isolated repair candidate")
    scheduled.add_argument("--recover-stopped-service", choices=("db", "backend"), action="append", default=[])
    scheduled.add_argument("--restore-config", action="store_true", help="Restore approved settings on a managed lab")
    scheduled.add_argument("--restore-image", action="store_true", help="Restore the approved image on a managed lab")
    scheduled.add_argument("--apply-verified-code", action="store_true", help="Promote reviewed fixes on a managed lab")
    fault = commands.add_parser("fault", help="List or run repeatable faults in a disposable FastAPI lab")
    scenarios = fault.add_subparsers(dest="action", required=True)
    scenarios.add_parser("list", help="Show the catalog and expected results")
    scenario = scenarios.add_parser("run", help="Run one scenario and compare its observed result")
    scenario.add_argument("scenario", choices=CATALOG)
    scenario.add_argument("--repo", type=Path, default=Path("../fastapi-target"))
    scenario.add_argument("--port", type=int, default=18007)
    scenario.add_argument("--repair-port", type=int, default=18008)
    scenario.add_argument("--gemini", action="store_true", help="Use a reviewed AI proposal for supported source faults")
    scenario.add_argument("--jmeter-home", type=Path, help="Extracted JMeter directory for the latency scenario")
    scenario.add_argument("--heal", action="store_true", help="Recover a supported deployment fault")
    batch = commands.add_parser("plant", help="Plant five seeded faults and run their isolated lab scenarios")
    batch.add_argument("--seed", type=int, help="Replay the selection and order from an earlier run")
    batch.add_argument("--repo", type=Path, default=Path("../fastapi-target"))
    batch.add_argument("--port", type=int, default=18007)
    batch.add_argument("--repair-port", type=int, default=18008)
    batch.add_argument("--gemini", action="store_true", help="Use reviewed AI proposals for supported source faults")
    batch.add_argument("--jmeter-home", type=Path, help="Include the latency scenario in the draw pool")
    load = commands.add_parser("load", help="Measure a disposable FastAPI deployment with Apache JMeter")
    load.add_argument("mode", choices=[*PROFILES, "compare"])
    load.add_argument("--jmeter-home", type=Path, help="Extracted JMeter directory (or set JMETER_HOME)")
    load.add_argument("--repo", type=Path, default=Path("../fastapi-target"))
    load.add_argument("--port", type=int, default=18009)
    for command in (controller, scheduled, watcher):
        command.add_argument("--performance", choices=REPAIR_PROFILES, help="Require two JMeter runs per deployment")
        command.add_argument("--jmeter-home", type=Path, help="Extracted JMeter directory (or set JMETER_HOME)")
    args = parser.parse_args()

    if args.command in {"serve", "worker"}:
        from .dashboard import DEFAULT_DB, serve, worker
        try:
            if args.command == "serve":
                serve(args.port, args.database or DEFAULT_DB, not args.no_worker)
            else:
                worker(args.database or DEFAULT_DB, args.once)
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Dashboard could not run: {exc}\n")
        return 0

    if args.command == "aws":
        from .aws_adapter import enroll, execute
        try:
            result = (enroll(args.stack, args.profile, args.region, args.output) if args.action == "enroll"
                      else execute(args.action, args.manifest))
        except (OSError, ValueError, KeyError) as exc:
            parser.exit(2, f"AWS operation could not complete: {exc}\n")
        emit_report(result, None)
        return 0 if result["status"] in {"enrolled", "healthy", "recovered", "fault_requested"} else 1

    if args.command == "load":
        try:
            result = run_load(args.mode, args.repo, args.jmeter_home, args.port,
                              lambda message: print(message, file=sys.stderr, flush=True))
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Load lab could not run: {exc}\n")
        emit_report(result, None)
        if any(trial.get("interrupted") for trial in result["trials"]):
            return 130
        return {"pass": 0, "measured": 0, "fail": 1, "inconclusive": 2}[result["status"]]

    if args.command == "plant":
        proposer = partial(propose_gemini, model=DEFAULT_MODEL) if args.gemini else None
        reviewer = partial(review_gemini, model=DEFAULT_REVIEW_MODEL) if args.gemini else None
        try:
            result = plant(args.repo, lambda message: print(message, file=sys.stderr, flush=True),
                           seed=args.seed, port=args.port, repair_port=args.repair_port,
                           proposer=proposer, reviewer=reviewer, jmeter_home=args.jmeter_home)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Plant 5 Breaks could not run: {exc}\n")
        emit_report(result, None)
        return {"matched": 0, "unexpected": 1, "inconclusive": 2, "interrupted": 130}[result["status"]]

    if args.command == "fault":
        if args.action == "list":
            emit_report(fault_catalog(), None)
            return 0
        proposer = partial(propose_gemini, model=DEFAULT_MODEL) if args.gemini else None
        reviewer = partial(review_gemini, model=DEFAULT_REVIEW_MODEL) if args.gemini else None
        try:
            result = run_fault(args.scenario, args.repo, args.port, args.repair_port,
                               proposer=proposer, reviewer=reviewer, heal=args.heal, jmeter_home=args.jmeter_home)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Fault lab could not run: {exc}\n")
        emit_report(result, None)
        if result.get("interrupted"):
            return 130
        return {"matched": 0, "unexpected": 1, "inconclusive": 2}[result["status"]]

    if args.command == "monitor":
        proposer = partial(propose_gemini, model=DEFAULT_MODEL) if args.gemini else None
        reviewer = partial(review_gemini, model=DEFAULT_REVIEW_MODEL) if args.gemini else None
        try:
            judge = make_performance_judge(args.performance, args.jmeter_home) if args.performance else None
            interval = args.interval if args.interval is not None else (300 if judge else 30)
            result = monitor(args.manifest, lambda report: print(json.dumps(report), flush=True),
                             interval=interval, once=args.once, proposer=proposer, reviewer=reviewer,
                             allowed=args.allow_path, port=args.port, judge_runner=judge,
                             recover_services=args.recover_stopped_service, restore_config=args.restore_config,
                             apply_code=args.apply_verified_code, restore_image=args.restore_image)
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Monitor could not run: {exc}\n")
        return {"healthy": 0, "recovered": 0, "inconclusive": 2}.get(result["status"], 1)

    if args.command == "ci":
        proposer = partial(propose_gemini, model=DEFAULT_MODEL) if args.gemini else None
        reviewer = partial(review_gemini, model=DEFAULT_REVIEW_MODEL) if args.gemini else None
        try:
            result = run_ci(args.repo, args.revision, allowed=args.allow_path, proposer=proposer, reviewer=reviewer)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"CI could not run: {exc}\n")
        emit_report(result, None)
        return {"pass": 0, "no_repair_needed": 0, "verified": 0, "fail": 1, "rejected": 1}.get(result["status"], 2)

    if args.command == "watch":
        proposer = partial(propose_gemini, model=DEFAULT_MODEL) if args.gemini else None
        reviewer = partial(review_gemini, model=DEFAULT_REVIEW_MODEL) if args.gemini else None
        try:
            if args.performance and not args.runtime:
                raise ValueError("--performance requires --runtime for local watch")
            judge = make_performance_judge(args.performance, args.jmeter_home) if args.performance else None
            result = watch(args.manifest, lambda report: print(json.dumps(report), flush=True),
                           debounce=args.debounce, once=args.once, runtime_port=args.port if args.runtime else None,
                           repair_port=args.repair_port if args.gemini else None, allowed=args.allow_path,
                           proposer=proposer, reviewer=reviewer, judge_runner=judge)
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Watch could not run: {exc}\n")
        return {"pass": 0, "verified": 0, "no_repair_needed": 0, "fail": 1, "rejected": 1}.get(result["status"], 2)

    if args.command == "publish":
        try:
            emit_report(publish(args.repair_report, args.repo, args.base_ref), None)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Repair could not be published: {exc}\n")
        return 0

    if args.command == "repair":
        try:
            proposer = partial(propose_gemini, model=args.model) if args.gemini else None
            review = args.review or (args.performance and args.gemini)
            reviewer = partial(review_gemini, model=args.review_model) if review else None
            judge = make_performance_judge(args.performance, args.jmeter_home) if args.performance else None
            result = repair(args.manifest, args.patch, args.allow_path, args.port, args.max_attempts,
                            proposer=proposer, reviewer=reviewer, judge=judge)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Repair could not run: {exc}\n")
        emit_report(result, None)
        return {"verified": 0, "no_repair_needed": 0, "rejected": 1, "inconclusive": 2}[result["status"]]

    if args.command == "verify":
        try:
            result = verify(read_object(args.baseline), read_object(args.candidate_report),
                            read_object(args.candidate_record), args.target_check)
        except (OSError, ValueError) as exc:
            result = {"command": "verify", "verdict": "inconclusive", "issues": [str(exc)]}
        try:
            emit_report(result, args.report)
        except OSError as exc:
            parser.exit(2, f"Could not write verification report: {exc}\n")
        return {"verified": 0, "rejected": 1, "inconclusive": 2}[result["verdict"]]

    if args.command == "candidate":
        try:
            if args.action == "prepare":
                result = prepare_candidate(args.manifest, args.patch, args.allow_path, args.port)
            elif args.action == "lab":
                result = prepare_clean_lab(args.manifest, args.port)
            else:
                result = run_candidate(args.project, args.action)
            emit_report(result, None)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Candidate could not {args.action}: {exc}\n")
        return 0

    if args.command in {"evidence", "snapshot"}:
        try:
            target = (args.compose, args.env_file, args.project)
            result = (collect_evidence(*target, args.log_lines) if args.command == "evidence"
                      else snapshot_config(*target))
            emit_report(result, args.report)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"Could not collect evidence: {exc}\n")
        return 0

    if args.command == "judge":
        report = judge_report(args.manifest)
    else:
        try:
            if args.command == "compare":
                results = compare(read_object(args.reference), read_object(args.candidate), args.allow_difference)
            else:
                results = check_python(args.source, args.python_version) + validate_config(read_object(args.config))
        except (OSError, ValueError) as exc:
            results = [check("runner/input", "error", str(exc))]
        report = summarize_checks(args.command, results)
    try:
        emit_report(report, getattr(args, "report", None))
    except OSError as exc:
        parser.exit(2, f"Could not write report: {exc}\n")
    return {"pass": 0, "fail": 1, "inconclusive": 2}[report["overall"]]


def emit_report(report, path):
    output = json.dumps(report, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    raise SystemExit(main())
