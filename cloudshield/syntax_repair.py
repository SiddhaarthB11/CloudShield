from pathlib import Path
from uuid import uuid4

from .candidate import (candidate_deployment, candidate_diff, commit_candidate, create_candidate_worktree,
                        git, patch_files, run_candidate, write_json)
from .checks import check_python
from .judge import judge_report
from .verifier import identity, read_checks, required_checks


def syntax_paths(checks):
    if any(row["status"] != "pass" and not (row["status"] == "fail" and row["id"].startswith("syntax/"))
           for row in checks):
        return set()
    return {"backend/app/" + row["id"].removeprefix("syntax/") for row in checks if row["status"] == "fail"}


def repair_syntax(target, revision, static, allowed, port, proposer, reviewer, judge=None, max_attempts=6):
    manifest, repo, compose_file, robot_suite = target
    paths = syntax_paths(static["checks"])
    root = Path("runs", "syntax-repairs", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    report_path = root / "syntax-repair.json"
    report = {"command": "syntax-repair", "status": "inconclusive", "baseline_revision": revision,
              "attempts": [], "report": str(report_path)}
    baseline = {**static, "mode": "syntax", "source_revision": revision}
    write_json(root / "baseline.json", baseline)
    docker_project = None
    try:
        if not paths or not paths <= set(allowed) or reviewer is None:
            raise ValueError("Syntax repair needs isolated, allowlisted Python failures and a Reviewer")
        scratch = create_candidate_worktree(repo, revision)
        source = scratch / "source"
        report["worktree"] = str(source)
        current, checks = revision, check_python(source / "backend/app", manifest["python_version"])
        if syntax_paths(checks) != paths:
            raise ValueError("The broken snapshot no longer matches the compiler report")
        trees = {git(source, "rev-parse", "HEAD^{tree}")}
        for number in range(1, max_attempts + 1):
            attempt = {"number": number}
            report["attempts"].append(attempt)
            patch, reason = proposer(source / "backend/app",
                                     {**baseline, "checks": checks, "source_revision": current},
                                     {}, sorted(paths)[:8], report["attempts"][:-1])
            attempt["summary"] = reason
            if not patch:
                raise ValueError("The Syntax Fixer did not propose a patch")
            touched = patch_files(source, patch, paths)
            git(source, "apply", "--index", "-", patch=patch)
            after = check_python(source / "backend/app", manifest["python_version"])
            remaining = syntax_paths(after)
            tree = git(source, "write-tree")
            if tree in trees or not remaining <= paths or any(row["status"] == "error" for row in after):
                git(source, "restore", "--source=HEAD", "--staged", "--worktree", "--", *sorted(touched))
                attempt["outcome"] = "rejected"
                attempt["reason"] = "The edit repeated a prior state or introduced a new compile failure"
                continue
            trees.add(tree)
            current = commit_candidate(source, "CloudShield syntax repair step")
            checks, paths = after, remaining
            attempt.update(outcome="compiled" if not paths else "partial",
                           remaining=[row for row in checks if row["status"] != "pass"])
            write_json(report_path, report)
            if not paths:
                break
        if paths:
            raise ValueError("Syntax repair exhausted its attempt budget")

        diff = candidate_diff(source, revision, current)
        (root / "repair.patch").write_bytes(diff)
        review = reviewer(baseline, diff)
        write_json(root / "review.json", review)
        if review["decision"] != "approve":
            report.update(status="rejected", detail="The Reviewer rejected the combined syntax repair")
            return report
        candidate = candidate_deployment(scratch, manifest, compose_file, robot_suite, revision, current, port)
        docker_project = candidate["project"]
        report.update(project=docker_project, candidate_revision=current)
        started = run_candidate(docker_project, "start")
        judged = (judge or judge_report)(candidate["manifest"])
        write_json(root / "candidate-judge.json", judged)
        statuses = read_checks(judged)
        if (judged.get("overall") != "pass" or not judged.get("deployment")
                or identity(judged["deployment"]) != identity(started["deployment"])
                or identity(judged["deployment"])["source_revision"] != current
                or required_checks(judged.get("test_profile")) - statuses.keys()
                or set(statuses.values()) != {"pass"}
                or any(row["id"] not in statuses for row in static["checks"])):
            report.update(status="rejected", detail="The exact repaired candidate did not pass all required checks")
        else:
            report["status"] = "verified"
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    finally:
        if docker_project:
            try:
                run_candidate(docker_project, "discard")
            except (OSError, ValueError) as exc:
                report.update(status="inconclusive", cleanup="failed", cleanup_detail=str(exc))
            else:
                report["cleanup"] = "discarded"
        write_json(report_path, report)
    return report
