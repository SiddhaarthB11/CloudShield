import hashlib
import json
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .checks import check, read_object, summarize_checks
from .evidence import docker_output
from .judge import judge_report, source_revision, verified_backend
from .performance import PLAN, PROFILES, jmeter_runtime, run_jmeter
from .robot_keywords import TargetChecks


REPAIR_PROFILES = {"normal": 4, "peak": 12}


def performance_judge(manifest, *, name, java, jar):
    report = judge_report(manifest)
    if not report.get("deployment") or report["overall"] == "inconclusive":
        return report

    check_id = f"performance/{name}"
    profile = PROFILES[name]
    budget = {**profile["budget"], "min_successful_requests_per_second": REPAIR_PROFILES[name]}
    root = Path("runs", "performance", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    measurements = {"check_id": check_id, "endpoint": "GET /api/v1/users/me",
                    "workload": {key: profile[key] for key in ("users", "ramp_seconds", "measure_seconds")},
                    "budget": budget, "runs": [], "repair_eligible": False,
                    "artifacts": str(root)}
    report["performance"] = measurements
    try:
        manifest = Path(manifest).resolve()
        target = read_object(manifest)
        base_url = target["base_url"]
        compose = manifest.parent / target["compose_file"]
        credentials = manifest.parent / target["credentials_file"]
        identity = report["deployment"]
        runtime, _ = docker_output(["inspect", "--type", "container", "--format",
                                    "{{json .Config.Cmd}} {{.HostConfig.NanoCpus}} {{.HostConfig.CpuQuota}} "
                                    "{{.HostConfig.CpuPeriod}} {{.HostConfig.Memory}}", identity["container_id"]],
                                   "performance target settings")
        profile_material = [json.dumps([name, profile, budget, runtime], sort_keys=True).encode(),
                            PLAN.read_bytes(), PLAN.with_name("results.properties").read_bytes(),
                            compose.read_bytes(), jar.read_bytes()]
        signature = hashlib.sha256(b"\0".join(profile_material)).hexdigest()
        report["test_profile"]["performance"] = {"check_id": check_id, "sha256": signature}

        token = TargetChecks(base_url, credentials).seeded_admin_token()
        violations = []
        for number in (1, 2):
            trial = root / f"trial-{number}"
            trial.mkdir()
            metrics = run_jmeter(java, jar, trial, profile, urlsplit(base_url).port, token)
            measurements["runs"].append(metrics)
            missed = set()
            if metrics["p95_ms"] is None or metrics["p95_ms"] > budget["p95_ms"]:
                missed.add("p95")
            if 100 * metrics["errors"] > budget["error_pct"] * metrics["samples"]:
                missed.add("errors")
            if metrics["successful_requests_per_second"] < budget["min_successful_requests_per_second"]:
                missed.add("throughput")
            violations.append(missed)
        revision = identity["source_revision"]
        if (source_revision(manifest.parent / target["source_dir"]) != revision
                or verified_backend(compose, credentials, target["compose_project"], base_url, revision) != identity):
            raise ValueError("The target source or deployment changed during JMeter")

        if not any(violations):
            result = check(check_id, "pass", "Both JMeter runs met the performance budget")
        elif violations[0] & violations[1]:
            measurements["repair_eligible"] = (report["overall"] == "pass"
                                                and all("p95" in missed and "errors" not in missed
                                                        for missed in violations))
            result = check(check_id, "fail", "Both JMeter runs missed: " + ", ".join(sorted(violations[0] & violations[1])))
        else:
            result = check(check_id, "error", "JMeter results did not reproduce the same budget breach")
    except (AssertionError, OSError, ValueError) as exc:
        result = check(check_id, "error", str(exc))
    report.update(summarize_checks("judge", [*report["checks"], result]))
    return report


def make_performance_judge(name, jmeter_home):
    java, jar = jmeter_runtime(jmeter_home)
    return partial(performance_judge, name=name, java=java, jar=jar)
