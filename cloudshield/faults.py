import json
from pathlib import Path
from uuid import uuid4

from .candidate import (candidate_deployment, candidate_diff, commit_candidate, create_candidate_worktree, git,
                        read_patch, run_candidate, validate_candidate_port, write_json)
from .checks import check_python, read_object, summarize_checks
from .controller import repair
from .evidence import collect_evidence, docker_output
from .incidents import diagnose
from .judge import judge_report
from .managed_lab import reconcile_config, replace_backend
from .recovery import plan_service_recovery, start_existing_services
from .syntax_repair import repair_syntax


RUNNER = Path(__file__).resolve().parent.parent
PINNED = "84c5a9e11a9e58262599e3d772f5386924e02ca7"
ROUTE = "backend/app/api/routes/utils.py"
AUTH = "backend/app/api/deps.py"
LOGIN = "backend/app/api/routes/login.py"
PROFILE = "backend/app/api/routes/users.py"
HEALTH = "robot/api-health"
DATABASE = "robot/database-backed-user-lookup"
ANONYMOUS = "robot/anonymous-user-denied"
STARTUP_MARKER = "cloudshield-phase16-startup-failure"
CATALOG = {
    "syntax": {"layer": "syntax", "check": "syntax/api/routes/utils.py", "outcome": "detected",
               "fault": "Leave an unclosed parenthesis in the health endpoint."},
    "app-logic": {"layer": "application", "check": HEALTH, "outcome": "verified",
                  "fault": "Return false from the health endpoint."},
    "authorization": {"layer": "application", "check": ANONYMOUS, "outcome": "verified",
                      "fault": "Return HTTP 418 instead of the approved anonymous access denial."},
    "login": {"layer": "application", "check": DATABASE, "outcome": "verified",
              "fault": "Pass the password as the username during authentication."},
    "latency": {"layer": "performance", "check": "performance/normal", "outcome": "verified",
                "fault": "Delay each authenticated profile read while preserving its response."},
    "regression": {"layer": "application", "check": ANONYMOUS, "outcome": "rejected",
                   "fault": "Restore health but change the anonymous request response to HTTP 418."},
    "config-drift": {"layer": "configuration", "check": "runtime/config/DB_SECRET_REF", "outcome": "handoff",
                     "fault": "Run the backend with a different database secret reference label."},
    "startup": {"layer": "startup", "check": "compose/backend-exit", "outcome": "handoff",
                "fault": "Make the backend process exit during startup."},
    "network": {"layer": "networking", "check": DATABASE, "outcome": "handoff",
                "fault": "Disconnect the running database from the lab network."},
    "dependency": {"layer": "dependency", "check": DATABASE, "outcome": "handoff",
                   "fault": "Stop the lab database while leaving the backend running."},
}
CODE_FAULTS = {"app-logic": (ROUTE, "examples/candidate-health.patch"),
               "authorization": (AUTH, "examples/faults/regression-auth.patch"),
               "login": (LOGIN, "examples/faults/login.patch"),
               "latency": (PROFILE, "examples/performance-slow.patch")}


def fault_catalog():
    return {"command": "fault list", "scenarios": [{"id": name, **scenario} for name, scenario in CATALOG.items()]}


def fault_compose(root, name):
    compose = RUNNER / "ci/compose.yml"
    if name not in {"config-drift", "startup"}:
        return compose
    text = compose.read_text(encoding="utf-8")
    if name == "config-drift":
        text = text.replace("${APP_DB_SECRET_REF:?Required}", "phase16/drift")
    else:
        command = ["python", "-c", f"import sys; print('{STARTUP_MARKER}', flush=True); sys.exit(23)"]
        text = text.replace("  backend:\n", f"  backend:\n    command: {json.dumps(command)}\n")
    copy = root / "compose.yml"
    copy.write_text(text, encoding="utf-8")
    return copy


def capture_lab_evidence(target, root):
    manifest_path = Path(target["manifest"])
    manifest = read_object(manifest_path)
    evidence = collect_evidence(manifest_path.parent / manifest["compose_file"],
                                manifest_path.parent / manifest["credentials_file"], target["project"], log_lines=10)
    write_json(root / "evidence.json", evidence)
    return evidence


def inject_db_fault(name, target, root):
    evidence = capture_lab_evidence(target, root)
    databases = [row for row in evidence["containers"] if row["service"] == "db" and row["state"] == "running"]
    if len(databases) != 1:
        raise ValueError("The isolated lab database must be running before injection")
    container = databases[0]["container_id"]
    if name == "dependency":
        docker_output(["stop", container], "lab database stop")
        return
    network = target["project"] + "_default"
    docker_output(["network", "disconnect", network, container], "lab database disconnect")
    output, _ = docker_output(["inspect", "--format", "{{json .NetworkSettings.Networks}}", container],
                              "lab database networks")
    if network in json.loads(output):
        raise ValueError("The lab database is still attached to the project network")


def startup_handoff(target, root):
    evidence = capture_lab_evidence(target, root)
    return any(row["service"] == "backend" and row["state"] == "exited" and row.get("exit_code") == 23
               and any(STARTUP_MARKER in line for line in row.get("logs", [])) for row in evidence["containers"])


def run_runtime(name, target, root, repair_port, proposer, reviewer, *, heal=False, judge_runner=None):
    try:
        run_candidate(target["project"], "start")
    except ValueError:
        if name == "startup" and startup_handoff(target, root):
            return {"outcome": "handoff", "observed": ["compose/backend-exit"],
                    "evidence_report": str(root / "evidence.json")}
        raise
    if name in CODE_FAULTS or name == "regression":
        allowed = [CODE_FAULTS.get(name, CODE_FAULTS["app-logic"])[0]]
        patches = None
        if proposer is None:
            if name in {"authorization", "login", "latency"}:
                fix = candidate_diff(target["worktree"], target["candidate_revision"], PINNED)
            else:
                fix = read_patch(RUNNER / "examples/repair-health.patch")
            if name == "regression":
                fix += read_patch(RUNNER / "examples/faults/regression-auth.patch")
                allowed.append(AUTH)
            patch = root / "repair.patch"
            patch.write_bytes(fix)
            patches = [patch]
        result = repair(target["manifest"], patches, allowed, repair_port, max_attempts=3 if proposer else 1,
                        proposer=proposer, reviewer=reviewer, judge=judge_runner)
        report = Path(result["report"])
        attempt = result["attempts"][-1] if result["attempts"] else {}
        regressions = attempt.get("regressions", [])
        resolved = []
        if attempt.get("stage") == "verify" and attempt.get("outcome") in {"verified", "rejected"}:
            verdict = read_object(report.parent / f"attempt-{attempt['number']}-verdict.json")
            resolved = verdict["resolved_targets"]
        outcome = {"outcome": result["status"], "observed": resolved + regressions, "repair_report": str(report),
                   "resolved": resolved, "regressions": regressions}
        if "diagnosis" in result:
            outcome["diagnosis"] = result["diagnosis"]
        if attempt.get("cleanup") == "failed":
            outcome["repair_cleanup"] = "failed"
        if result.get("interrupted"):
            outcome["interrupted"] = True
        return outcome

    if name == "config-drift" and heal:
        healthy = judge_report(target["manifest"])
        if healthy["overall"] != "pass":
            raise ValueError("The lab must pass before planting configuration drift")
        manifest = Path(target["manifest"])
        settings = read_object(manifest)
        compose = (manifest.parent / settings["compose_file"], manifest.parent / settings["credentials_file"],
                   target["project"])
        replace_backend(compose, root, healthy["deployment"]["image_id"], drift=True)
    if name in {"dependency", "network"}:
        inject_db_fault(name, target, root)
    judge = judge_report(target["manifest"])
    write_json(root / "judge.json", judge)
    evidence = capture_lab_evidence(target, root)
    if name in {"dependency", "network"}:
        expected_state = "exited" if name == "dependency" else "running"
        if not any(row["service"] == "db" and row["state"] == expected_state for row in evidence["containers"]):
            raise ValueError(f"Expected the lab database to be {expected_state} after injection")
    observed = [row["id"] for row in judge["checks"] if row["status"] in {"fail", "connectivity"}]
    result = {"outcome": "handoff", "observed": observed, "judge_report": str(root / "judge.json"),
              "diagnosis": diagnose(judge["checks"], evidence["containers"]),
              "evidence_report": str(root / "evidence.json")}
    recovery_check = "runtime/config/DB_HOST" if name == "config-drift" else DATABASE
    if heal and name in {"config-drift", "dependency"} and recovery_check in observed:
        manifest = Path(target["manifest"])
        settings = read_object(manifest)
        compose = (manifest.parent / settings["compose_file"], manifest.parent / settings["credentials_file"],
                   target["project"])
        if name == "config-drift":
            recovered = reconcile_config(manifest, compose, judge, root, judge_report)
        else:
            plan = plan_service_recovery(evidence["containers"], {"db"})
            if not plan:
                raise ValueError("Could not plan a scoped database start")
            start_existing_services(compose, plan, target["candidate_revision"],
                                    lambda: write_json(root / "actions.json", plan))
            recovered = judge_report(manifest)
        write_json(root / "recovery-judge.json", recovered)
        if (recovered["overall"] == "pass"
                and recovered.get("deployment", {}).get("source_revision") == target["candidate_revision"]):
            result["outcome"] = "recovered"
        result["recovery_judge"] = str(root / "recovery-judge.json")
    return result


def run_fault(name, repo, port=18007, repair_port=18008, *, proposer=None, reviewer=None, heal=False, jmeter_home=None):
    scenario = dict(CATALOG[name])
    if proposer is not None and (name not in {*CODE_FAULTS, "syntax"} or reviewer is None):
        raise ValueError("Gemini is supported for source faults and requires review")
    if heal and name in {"config-drift", "dependency"}:
        scenario["outcome"] = "recovered"
        if name == "config-drift":
            scenario["check"] = "runtime/config/DB_HOST"
    if name == "syntax" and proposer is not None:
        scenario["outcome"] = "verified"
    if name != "syntax" or proposer is not None:
        validate_candidate_port(port)
        validate_candidate_port(repair_port)
        if port == repair_port:
            raise ValueError("Use different ports for the fault and repair deployments")
    root = Path("runs", "faults", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    report_path = root / "fault.json"
    report = {"command": "fault run", "scenario": name, "expected": scenario,
              "base_revision": PINNED, "report": str(report_path)}
    project = None
    try:
        judge_runner = None
        if name == "latency":
            from .performance_gate import make_performance_judge
            judge_runner = make_performance_judge("normal", jmeter_home)
        sandbox = create_candidate_worktree(Path(repo).resolve(), PINNED)
        source = sandbox / "source"
        report["worktree"] = str(source)
        revision = PINNED
        if name in {*CODE_FAULTS, "syntax", "regression"}:
            patch = RUNNER / ("examples/faults/syntax.patch" if name == "syntax"
                              else CODE_FAULTS.get(name, CODE_FAULTS["app-logic"])[1])
            git(source, "apply", "--index", "-", patch=read_patch(patch))
            revision = commit_candidate(source, f"CloudShield lab fault: {name}")
        report["fault_revision"] = revision
        if name == "syntax":
            checked = summarize_checks("static", check_python(source / "backend/app", "3.14"))
            write_json(root / "static.json", checked)
            report.update(outcome="inconclusive" if checked["overall"] == "inconclusive" else "detected",
                          observed=[row["id"] for row in checked["checks"] if row["status"] == "fail"],
                          diagnosis=diagnose(checked["checks"]),
                          static_report=str(root / "static.json"))
            if proposer is not None and checked["overall"] == "fail":
                manifest = read_object(RUNNER / "examples/target.json")
                result = repair_syntax((manifest, source, RUNNER / "ci/compose.yml", RUNNER / "suites/target.robot"),
                                       revision, checked, [ROUTE], repair_port, proposer, reviewer)
                report.update(outcome=result["status"], syntax_repair_report=result["report"])
                if result.get("cleanup") == "failed":
                    report["repair_cleanup"] = "failed"
        else:
            manifest = read_object(RUNNER / "examples/target.json")
            compose = RUNNER / "ci/compose.yml" if heal and name == "config-drift" else fault_compose(root, name)
            target = candidate_deployment(sandbox, manifest, compose, RUNNER / "suites/target.robot",
                                          PINNED, revision, port)
            project = target["project"]
            report["project"] = project
            report.update(run_runtime(name, target, root, repair_port, proposer, reviewer, heal=heal,
                                      judge_runner=judge_runner))
        if report["outcome"] == "inconclusive":
            report["status"] = "inconclusive"
        else:
            observed = scenario["check"] in report["observed"]
            if name == "regression":
                observed = HEALTH in report["resolved"] and ANONYMOUS in report["regressions"]
            matched = report["outcome"] == scenario["outcome"] and observed
            report["status"] = "matched" if matched else "unexpected"
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    except KeyboardInterrupt:
        report.update(status="inconclusive", interrupted=True, detail="Fault run interrupted")
    finally:
        if project is not None:
            try:
                run_candidate(project, "discard")
            except (OSError, ValueError) as exc:
                report.update(status="inconclusive", cleanup="failed", cleanup_detail=str(exc))
            else:
                report["cleanup"] = "discarded"
        write_json(report_path, report)
    return report
