from pathlib import Path
from uuid import uuid4

from .candidate import (allowed_source_path, candidate_deployment, create_candidate_worktree,
                        run_candidate, write_json)
from .checks import check_python, read_object, summarize_checks, validate_config
from .controller import repair
from .evidence import REVISION
from .judge import judge_report, source_revision


def run_ci(repo, revision, *, allowed=(), proposer=None, reviewer=None):
    repo = Path(repo).resolve()
    if not REVISION.fullmatch(revision) or source_revision(repo) != revision:
        raise ValueError("CI must check out the exact commit from the GitHub event")
    if proposer is not None and (reviewer is None or not allowed or not all(map(allowed_source_path, allowed))):
        raise ValueError("CI repair requires a Reviewer and explicit allowed application Python paths")

    runner = Path(__file__).resolve().parent.parent
    root = Path("runs", "ci", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    path = root / "ci.json"
    report = {"command": "ci", "status": "running", "event_revision": revision, "report": str(path)}
    project = None
    try:
        sandbox = create_candidate_worktree(repo, revision)
        source = sandbox / "source"
        if source_revision(source) != revision:
            raise ValueError("CI sandbox does not match the event commit")
        config = read_object(runner / "examples/reference.json")
        checks = check_python(source / "backend/app", "3.14") + validate_config(config)
        static = summarize_checks("static", checks)
        write_json(root / "static.json", static)
        report.update(static_report=str(root / "static.json"), status=static["overall"])
        if static["overall"] != "pass":
            return report

        manifest = {"python_version": "3.14", "required_robot_checks": [
            "api-health", "anonymous-user-denied", "database-backed-user-lookup",
        ]}
        baseline = candidate_deployment(sandbox, manifest, runner / "ci/compose.yml", runner / "suites/target.robot",
                                        revision, revision, 18004)
        project = baseline["project"]
        report.update(project=project, status="running")
        write_json(path, report)
        run_candidate(project, "start")
        if proposer is not None:
            result = repair(baseline["manifest"], None, allowed, 18005, max_attempts=3,
                            proposer=proposer, reviewer=reviewer)
            report.update(status=result["status"], repair_report=result["report"])
            if any(attempt["cleanup"] == "failed" for attempt in result["attempts"]):
                report.update(status="inconclusive", repair_cleanup="failed")
        else:
            judged = judge_report(baseline["manifest"])
            write_json(root / "judge.json", judged)
            report.update(status=judged["overall"], judge_report=str(root / "judge.json"))
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    finally:
        if project is not None:
            try:
                run_candidate(project, "discard")
            except (OSError, ValueError) as exc:
                report.update(status="inconclusive", cleanup="failed", cleanup_detail=str(exc))
            else:
                report["cleanup"] = "discarded"
        write_json(path, report)
    return report
