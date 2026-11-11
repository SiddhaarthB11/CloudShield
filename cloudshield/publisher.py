import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .candidate import candidate_diff, git, read_patch, write_json
from .checks import read_object
from .verifier import verify


def publish(repair_path, repo, base_ref):
    """Publish the exact verified commit as a local branch."""
    repair_path = Path(repair_path).resolve()
    root = repair_path.parent
    if repair_path.name != "repair.json" or not re.fullmatch(r"[0-9a-f]{12}", root.name):
        raise ValueError("Use repair.json from a CloudShield repair run directory")
    repair = read_object(repair_path)
    attempts = repair.get("attempts")
    if (repair.get("command") != "repair" or repair.get("status") != "verified"
            or not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict)):
        raise ValueError("Publish requires a completed, verified repair")
    attempt = attempts[-1]
    number = attempt.get("number")
    if type(number) is not int or number != len(attempts) or attempt.get("outcome") != "verified":
        raise ValueError("The final repair attempt is not verified")

    files = {"repair": "repair.json", "baseline": "baseline.json", "evidence": "baseline-evidence.json",
             "patch": f"attempt-{number}.patch", "deployment": f"attempt-{number}-deployment.json",
             "candidate_checks": f"attempt-{number}-judge.json", "verdict": f"attempt-{number}-verdict.json"}
    deployment = read_object(root / files["deployment"])
    verdict = verify(read_object(root / files["baseline"]), read_object(root / files["candidate_checks"]), deployment)
    if verdict["verdict"] != "verified" or verdict != read_object(root / files["verdict"]):
        raise ValueError("Saved results do not reproduce a verified verdict")
    before = verdict["baseline_deployment"]["source_revision"]
    after = verdict["candidate_deployment"]["source_revision"]
    patch_hash = hashlib.sha256(read_patch(root / files["patch"])).hexdigest()
    if (attempt.get("candidate_revision") != after or attempt.get("project") != deployment.get("project")
            or attempt.get("patch_sha256") != patch_hash or deployment.get("patch_sha256") != patch_hash):
        raise ValueError("Saved attempt, deployment, and patch do not match")
    read_object(root / files["evidence"])

    repo = Path(repo).resolve()
    branch = f"cloudshield/repairs/{root.name}"
    repair_ref = f"refs/heads/{branch}"
    if (not base_ref.startswith(("refs/heads/", "refs/remotes/"))
            or base_ref.startswith("refs/heads/cloudshield/repairs/")):
        raise ValueError("Choose the full ref of a local or remote-tracking base branch")
    git(repo, "check-ref-format", base_ref)
    if git(repo, "rev-list", "--parents", "-n", "1", after).split() != [after.encode(), before.encode()]:
        raise ValueError("The verified candidate must be a direct child of the failing revision")
    diff = candidate_diff(repo, before, after)
    diff_hash = hashlib.sha256(diff).hexdigest()
    review = attempt.get("review")
    if repair.get("review_required") or review is not None:
        files.update(review=f"attempt-{number}-review.json", reviewed_patch=f"attempt-{number}-reviewed.patch")
        if (not isinstance(review, dict) or review != read_object(root / files["review"])
                or review.get("decision") != "approve" or review.get("candidate_revision") != after
                or review.get("diff_sha256") != diff_hash or diff != read_patch(root / files["reviewed_patch"])):
            raise ValueError("The approval does not match the verified Git diff")

    # Verify the base and create the branch in the same Git ref transaction.
    check_base = f"verify {base_ref} {before}\n"
    try:
        git(repo, "update-ref", "--stdin", patch=f"{check_base}create {repair_ref} {after}\n".encode())
    except ValueError:
        try:
            git(repo, "update-ref", "--stdin", patch=f"{check_base}verify {repair_ref} {after}\n".encode())
        except ValueError:
            current = git(repo, "rev-parse", "--verify", base_ref).decode().strip()
            if current != before:
                raise ValueError("The base branch advanced. Verify a new repair against its current revision.") from None
            raise ValueError("Could not create the repair branch or confirm it already points to the verified commit") from None

    report_path = root / "publication.json"
    result = {"command": "publish", "status": "published", "scope": "local_git",
              "repository": str(repo), "base_ref": base_ref, "baseline_revision": before,
              "branch": branch, "candidate_revision": after, "candidate_deployment": verdict["candidate_deployment"],
              "attempt": number, "patch_sha256": patch_hash, "diff_sha256": diff_hash, "review": review,
              "resolved_targets": verdict["resolved_targets"], "regressions": verdict["regressions"],
              "artifacts": files, "published_at": datetime.now(timezone.utc).isoformat(),
              "report": str(report_path)}
    scratch = root / f".publication-{uuid4().hex}.tmp"
    write_json(scratch, result)
    scratch.replace(report_path)
    return result
