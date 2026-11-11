import re

from .checks import CONFIG_TYPES
from .evidence import CONTAINER_ID, IMAGE_ID, REVISION


STATUSES = {"pass", "fail", "skip", "error", "connectivity"}
IDENTITY_FIELDS = {"source_revision": REVISION, "image_id": IMAGE_ID, "container_id": CONTAINER_ID}


def read_checks(report):
    if report.get("command") != "judge" or not isinstance(report.get("checks"), list):
        raise ValueError("Expected a JSON report from the Judge")
    checks = {}
    for row in report["checks"]:
        if not isinstance(row, dict):
            raise ValueError("Judge report has an invalid check")
        name, status = row.get("id"), row.get("status")
        if not isinstance(name, str) or not name.strip() or name in checks:
            raise ValueError("Judge report has a missing or duplicate check ID")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"Unknown status for check {name}")
        checks[name] = status
    return checks


def identity(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a deployment identity in both Judge reports and the candidate start record")
    fields = {}
    for name, pattern in IDENTITY_FIELDS.items():
        item = value.get(name)
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise ValueError(f"Deployment has no full {name}")
        fields[name] = item
    return fields


def required_checks(profile):
    if not isinstance(profile, dict):
        raise ValueError("Judge report has no test profile. Rerun the Judge with Phase 8 code.")
    for key in ("suite_sha256", "keywords_sha256"):
        value = profile.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Judge report has an invalid test profile")
    version = profile.get("python_version")
    names = profile.get("required_robot_checks")
    if (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", version)
            or not isinstance(names, list) or not names
            or not all(isinstance(name, str) and name.strip() for name in names) or len(set(names)) != len(names)):
        raise ValueError("Judge report has an invalid Python version or required check list")
    required = {"deployment/identity", *(f"robot/{name}" for name in names),
                *(f"config/{key}" for key in CONFIG_TYPES), *(f"runtime/config/{key}" for key in CONFIG_TYPES)}
    if "performance" in profile:
        performance = profile["performance"]
        if (not isinstance(performance, dict) or performance.get("check_id") not in ("performance/normal", "performance/peak")
                or not isinstance(performance.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", performance["sha256"])):
            raise ValueError("Judge report has an invalid performance profile")
        required.add(performance["check_id"])
    return required


def verify(baseline_report, candidate_report, candidate_record, targets=()):
    baseline = read_checks(baseline_report)
    candidate = read_checks(candidate_report)
    before = identity(baseline_report.get("deployment"))
    after = identity(candidate_report.get("deployment"))
    expected = identity(candidate_record.get("deployment"))
    profile = baseline_report.get("test_profile")
    required = required_checks(profile)
    required_checks(candidate_report.get("test_profile"))

    issues = []
    if candidate_record.get("status") != "started":
        issues.append("Expected the deployment.json record from candidate start")
    if before["source_revision"] != candidate_record.get("baseline_revision"):
        issues.append("Baseline report does not match the revision the candidate was created from")
    if after != expected or after["source_revision"] != candidate_record.get("candidate_revision"):
        issues.append("Candidate report does not match the exact recorded candidate deployment")
    if any(before[field] == after[field] for field in IDENTITY_FIELDS):
        issues.append("Candidate and baseline must use different revisions, images, and containers")
    if profile != candidate_report.get("test_profile"):
        issues.append("Baseline and candidate used different test definitions or runner settings")
    for label, report, checks in (("Baseline", baseline_report, baseline), ("Candidate", candidate_report, candidate)):
        expected_overall = "fail" if "fail" in checks.values() else "pass"
        if report.get("overall") != expected_overall or checks.get("deployment/identity") != "pass":
            issues.append(f"{label} Judge run did not establish a valid deployment and usable results")
        for name in sorted(required - checks.keys()):
            issues.append(f"{label} is missing required check {name}")
        for name, status in sorted(checks.items()):
            if status not in {"pass", "fail"}:
                issues.append(f"{label} check {name} has no usable result ({status})")

    target_ids = sorted(set(targets) or {name for name, status in baseline.items() if status == "fail"})
    for name in target_ids:
        if baseline.get(name) != "fail":
            issues.append(f"Target check {name} did not fail in the baseline")
    for name in sorted(baseline.keys() - candidate.keys() - required):
        issues.append(f"Candidate is missing baseline check {name}")

    regressions = sorted(name for name, status in baseline.items() if status == "pass" and candidate.get(name) == "fail")
    resolved = [name for name in target_ids if baseline.get(name) == "fail" and candidate.get(name) == "pass"]
    unresolved = [name for name in target_ids if name not in resolved]
    failures = sorted(name for name, status in candidate.items() if status == "fail")
    if not target_ids and not failures:
        issues.append("Baseline has no confirmed failure to verify as repaired")

    verdict = "inconclusive" if issues else "rejected" if failures else "verified"
    return {"command": "verify", "verdict": verdict, "baseline_deployment": before, "candidate_deployment": after,
            "target_checks": target_ids, "resolved_targets": resolved, "unresolved_targets": unresolved,
            "regressions": regressions, "candidate_failures": failures, "issues": issues}
