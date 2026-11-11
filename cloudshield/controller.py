import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .candidate import candidate_diff, prepare_candidate, read_patch, run_candidate, validate_candidate_port, write_json
from .checks import read_object
from .evidence import collect_evidence, running_backend
from .incidents import diagnose
from .judge import judge_report
from .repair_memory import memory_key, repair_history
from .verifier import identity, read_checks, required_checks, verify


def record(root, report):
    scratch = root / "repair.tmp"
    write_json(scratch, report)
    scratch.replace(root / "repair.json")


def baseline_evidence(manifest_path, manifest, deployment):
    compose = manifest_path.parent / manifest["compose_file"]
    credentials = manifest_path.parent / manifest["credentials_file"]
    evidence = collect_evidence(compose, credentials, manifest["compose_project"], log_lines=10)
    if identity(running_backend(evidence)) != deployment:
        raise ValueError("The baseline deployment changed after the Judge run")
    return evidence


def attempt_repair(root, manifest, baseline, patch, allowed, port, attempt, report, reviewer, judge):
    project = None
    docker_attempted = False
    try:
        attempt["stage"] = "prepare"
        record(root, report)
        candidate = prepare_candidate(manifest, patch, allowed, port,
                                      expected_revision=baseline["deployment"]["source_revision"])
        project = candidate["project"]
        attempt.update(project=project, candidate_revision=candidate["candidate_revision"])
        if "manifest" in candidate:
            attempt["candidate_manifest"] = candidate["manifest"]
        if reviewer is not None:
            attempt["stage"] = "review"
            record(root, report)
            diff = candidate_diff(candidate["worktree"], candidate["baseline_revision"], candidate["candidate_revision"])
            number = attempt["number"]
            (root / f"attempt-{number}-reviewed.patch").write_bytes(diff)
            review = reviewer(baseline, diff)
            review_record = {**review, "candidate_revision": candidate["candidate_revision"],
                             "diff_sha256": hashlib.sha256(diff).hexdigest()}
            write_json(root / f"attempt-{number}-review.json", review_record)
            attempt["review"] = review_record
            if review["decision"] != "approve":
                attempt["outcome"] = "rejected"
                return

        attempt["stage"] = "start"
        record(root, report)
        docker_attempted = True
        deployment = run_candidate(project, "start")
        write_json(root / f"attempt-{attempt['number']}-deployment.json", deployment)

        attempt["stage"] = "judge"
        record(root, report)
        judged = judge(candidate["manifest"])
        write_json(root / f"attempt-{attempt['number']}-judge.json", judged)

        attempt["stage"] = "verify"
        record(root, report)
        verdict = verify(baseline, judged, deployment)
        write_json(root / f"attempt-{attempt['number']}-verdict.json", verdict)
        attempt["outcome"] = verdict["verdict"]
        for field in ("regressions", "unresolved_targets"):
            attempt[field] = verdict[field]
    except (OSError, ValueError) as exc:
        attempt.update(outcome="inconclusive", detail=str(exc))
    finally:
        if docker_attempted:
            try:
                run_candidate(project, "discard")
            except (OSError, ValueError) as exc:
                attempt.update(cleanup="failed", cleanup_detail=str(exc))
            else:
                attempt["cleanup"] = "discarded"
        record(root, report)


def repair(manifest, patches, allowed, port=18002, max_attempts=2, *, proposer=None, reviewer=None,
           expected_deployment=None, judge=None):
    if not 1 <= max_attempts <= 5:
        raise ValueError("Allow between 1 and 5 attempts")
    validate_candidate_port(port)
    judge = judge or judge_report
    manifest = Path(manifest).resolve()
    root = Path("runs", "repairs", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"command": "repair", "status": "running", "stage": "baseline", "attempts": [],
              "patch_source": "gemini" if proposer else "provided",
              "review_required": reviewer is not None,
              "started_at": datetime.now(timezone.utc).isoformat(), "report": str(root / "repair.json")}
    record(root, report)
    try:
        baseline = judge(manifest)
        write_json(root / "baseline.json", baseline)
        if expected_deployment is not None and baseline.get("deployment") != expected_deployment:
            raise ValueError("The monitored deployment changed before repair. Observe it again.")
        if baseline["overall"] == "pass":
            report["status"] = "no_repair_needed"
            return report
        if baseline["overall"] != "fail" or not baseline.get("deployment") or not baseline.get("test_profile"):
            raise ValueError("Repair requires a confirmed failure on an identified, tested deployment")
        checks = read_checks(baseline)
        if (checks.get("deployment/identity") != "pass" or "fail" not in checks.values()
                or required_checks(baseline["test_profile"]) - checks.keys()
                or any(status not in {"pass", "fail"} for status in checks.values())):
            raise ValueError("Baseline does not have usable results for all required checks")
        target = read_object(manifest)
        fields = ("base_url", "compose_file", "credentials_file", "compose_project")
        if proposer:
            fields += ("source_dir",)
        if any(not isinstance(target.get(field), str) or not target[field].strip() for field in fields):
            raise ValueError("Target manifest is missing deployment settings")
        if urlsplit(target["base_url"]).port == port:
            raise ValueError("Use a different port for the candidate and the baseline")

        report["stage"] = "evidence"
        record(root, report)
        evidence = baseline_evidence(manifest, target, identity(baseline["deployment"]))
        write_json(root / "baseline-evidence.json", evidence)
        report["diagnosis"] = diagnose(baseline["checks"], evidence.get("containers", ()))
        report["stage"] = "attempts"
        seen = set()
        report["duplicate_patches_skipped"] = 0
        report["status"] = "rejected"
        key = None
        history = {}
        best = None
        remaining = {name for name, status in checks.items() if status == "fail"}
        if proposer:
            report["fixer_proposals"] = []
            source_dir = (manifest.parent / target["source_dir"]).resolve()
            if source_dir.is_dir():
                key = memory_key(source_dir, baseline)
                history = repair_history(key)
        for supplied in (range(max_attempts) if proposer else patches):
            if len(report["attempts"]) >= max_attempts:
                break
            if proposer:
                report["stage"] = "fixer"
                record(root, report)
                proposal_source, proposal_baseline = source_dir, baseline
                if best:
                    best_manifest, proposal_baseline = best
                    proposal_source = best_manifest.parent / read_object(best_manifest)["source_dir"]
                patch_bytes, summary = proposer(proposal_source, proposal_baseline, evidence, allowed,
                                                [*history.values(), *report["attempts"]])
                proposal = {"summary": summary, "status": "proposed" if patch_bytes else "no_patch"}
                report["fixer_proposals"].append(proposal)
                if not patch_bytes:
                    raise ValueError("The Fixer did not propose a safe patch. See fixer_proposals in the run report.")
                if best:
                    step = root / "incremental.patch"
                    step.write_bytes(patch_bytes)
                    merged = prepare_candidate(best_manifest, step, allowed, port,
                                               expected_revision=proposal_baseline["deployment"]["source_revision"])
                    patch_bytes = candidate_diff(merged["worktree"], baseline["deployment"]["source_revision"],
                                                 merged["candidate_revision"])
            else:
                patch_bytes = read_patch(supplied)
            digest = hashlib.sha256(patch_bytes).hexdigest()
            if digest in seen or digest in history:
                report["duplicate_patches_skipped"] += 1
                if proposer:
                    proposal["status"] = "duplicate"
                continue
            seen.add(digest)
            number = len(report["attempts"]) + 1
            saved_patch = root / f"attempt-{number}.patch"
            saved_patch.write_bytes(patch_bytes)
            attempt = {"number": number, "patch_sha256": digest, "cleanup": "not_started"}
            report["attempts"].append(attempt)
            report["status"] = "running"
            report["stage"] = "attempts"
            attempt_repair(root, manifest, baseline, saved_patch, allowed, port, attempt, report, reviewer, judge)
            if attempt["cleanup"] == "failed":
                raise ValueError(f"Could not clean up {attempt['project']}. Retry candidate discard before another run.")
            report["status"] = attempt["outcome"]
            if proposer and attempt["outcome"] == "rejected":
                unresolved = set(attempt.get("unresolved_targets", ()))
                if (attempt["stage"] == "verify" and not attempt["regressions"]
                        and unresolved < remaining and attempt.get("candidate_manifest")):
                    remaining = unresolved
                    best = (Path(attempt["candidate_manifest"]), read_object(root / f"attempt-{number}-judge.json"))
                    attempt["retained_progress"] = True
                elif key:
                    repair_history(key, attempt)
            if attempt["outcome"] == "verified" or (
                    attempt["stage"] in {"review", "verify"} and attempt["outcome"] == "inconclusive"):
                break
        if not report["attempts"]:
            raise ValueError("No usable patch was supplied")
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    except KeyboardInterrupt:
        report.update(status="inconclusive", detail="Repair interrupted", interrupted=True)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        record(root, report)
    return report
