import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .candidate import candidate_deployment, create_candidate_worktree, run_candidate, validate_candidate_port, write_json
from .checks import read_object
from .faults import PINNED, RUNNER
from .judge import verified_backend
from .robot_keywords import TargetChecks


PROFILES = {
    "normal": {"users": 2, "ramp_seconds": 2, "measure_seconds": 15, "budget": {"p95_ms": 300, "error_pct": 1}},
    "peak": {"users": 8, "ramp_seconds": 4, "measure_seconds": 20, "budget": {"p95_ms": 500, "error_pct": 1}},
    "stress": {"users": 32, "ramp_seconds": 5, "measure_seconds": 25, "budget": None},
}
PLAN = RUNNER / "performance/profile-read.jmx"
LABEL = "Read synthetic user profile"


def summarize_samples(path, profile):
    samples = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"timeStamp", "elapsed", "label", "success"} <= set(reader.fieldnames or []):
            raise ValueError("JMeter did not produce the required CSV sample fields")
        for row in reader:
            if row["label"] != LABEL:
                raise ValueError("JMeter output contains an unexpected sampler")
            try:
                started, elapsed = int(row["timeStamp"]), int(row["elapsed"])
                if started <= 0 or elapsed < 0 or row["success"] not in {"true", "false"}:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("JMeter output contains an invalid sample") from None
            samples.append((started, elapsed, row["success"] == "true"))
    if not samples:
        raise ValueError("JMeter did not record any samples")
    duration = profile["measure_seconds"]
    window_ms = duration * 1000
    start = min(stamp for stamp, _, _ in samples) + profile["ramp_seconds"] * 1000
    measured = [sample for sample in samples if start <= sample[0] < start + window_ms]
    if not measured or max(stamp + elapsed for stamp, elapsed, _ in measured) < start + 0.8 * window_ms:
        raise ValueError("JMeter output did not cover the steady-load measurement window")
    latencies = sorted(elapsed for _, elapsed, passed in measured if passed)
    count, successes = len(measured), len(latencies)
    p95 = latencies[math.ceil(0.95 * successes) - 1] if latencies else None
    return {"samples": count, "successful": successes, "errors": count - successes,
            "error_pct": round((count - successes) * 100 / count, 3),
            "requests_per_second": round(count / duration, 3),
            "successful_requests_per_second": round(successes / duration, 3), "p95_ms": p95,
            "ramp_or_outside_window_samples": len(samples) - count}


def run_jmeter(java, jar, trial, profile, port, token):
    samples = trial / "samples.jtl"
    log = trial / "jmeter.log"
    duration = profile["ramp_seconds"] + profile["measure_seconds"]
    command = [java, "-Djava.awt.headless=true", "-jar", str(jar), "-n", "-t", str(PLAN),
               "-q", str(PLAN.with_name("results.properties")), "-l", str(samples), "-j", str(log),
               f"-Jport={port}", f"-Jusers={profile['users']}", f"-Jramp={profile['ramp_seconds']}",
               f"-Jduration={duration}"]
    env = {**os.environ, "CLOUDSHIELD_LOAD_TOKEN": token}
    try:
        result = subprocess.run(command, cwd=trial, env=env, capture_output=True, timeout=duration + 60, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("JMeter did not finish within the allotted time") from None
    if result.returncode or not samples.is_file():
        raise ValueError(f"JMeter did not produce a sample file (exit {result.returncode}). See {log}")
    return summarize_samples(samples, profile)


def jmeter_runtime(jmeter_home):
    java = shutil.which("java")
    if not java:
        raise ValueError("Install Java and make java available on PATH")
    home = jmeter_home or os.environ.get("JMETER_HOME")
    if not home:
        raise ValueError("Provide --jmeter-home or set JMETER_HOME to the extracted Apache JMeter folder")
    jar = Path(home).resolve() / "bin/ApacheJMeter.jar"
    if not jar.is_file():
        raise ValueError("Expected bin/ApacheJMeter.jar inside the JMeter home folder")
    return java, jar


def run_trial(repo, root, profile, workers, port, java, jar):
    trial = root / f"workers-{workers}"
    trial.mkdir()
    report = {"status": "running", "workers": workers, "report": str(trial / "trial.json")}
    project = None
    try:
        sandbox = create_candidate_worktree(repo, PINNED)
        compose = trial / "compose.yml"
        command = json.dumps(["fastapi", "run", "--workers", str(workers)])
        compose.write_text((RUNNER / "ci/compose.yml").read_text(encoding="utf-8").replace(
            "  backend:\n", f"  backend:\n    command: {command}\n"), encoding="utf-8")
        manifest = read_object(RUNNER / "examples/target.json")
        target = candidate_deployment(sandbox, manifest, compose, RUNNER / "suites/target.robot", PINNED, PINNED, port)
        project = target["project"]
        report["project"] = project
        deployment = run_candidate(project, "start")["deployment"]
        report["deployment"] = deployment
        base_url = f"http://127.0.0.1:{port}"
        credentials = sandbox / "candidate.env"
        token = TargetChecks(base_url, credentials).seeded_admin_token()
        report.update(samples=str(trial / "samples.jtl"), jmeter_log=str(trial / "jmeter.log"))
        metrics = run_jmeter(java, jar, trial, profile, port, token)
        if verified_backend(compose, credentials, project, base_url, PINNED) != deployment:
            raise ValueError("The lab backend changed during the load test")
        report["metrics"] = metrics
        budget = profile["budget"]
        if budget is None:
            report["status"] = "measured"
        elif (metrics["p95_ms"] is not None and metrics["p95_ms"] <= budget["p95_ms"]
              and 100 * metrics["errors"] <= budget["error_pct"] * metrics["samples"]):
            report["status"] = "pass"
        else:
            report["status"] = "fail"
    except (OSError, ValueError, AssertionError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    except KeyboardInterrupt:
        report.update(status="inconclusive", interrupted=True, detail="Load test interrupted")
    finally:
        if project is not None:
            try:
                run_candidate(project, "discard")
            except (OSError, ValueError) as exc:
                report.update(status="inconclusive", cleanup="failed", cleanup_detail=str(exc))
            else:
                report["cleanup"] = "discarded"
        write_json(trial / "trial.json", report)
    return report


def run_load(mode, repo, jmeter_home, port, emit):
    validate_candidate_port(port)
    java, jar = jmeter_runtime(jmeter_home)

    name = "stress" if mode == "compare" else mode
    profile = PROFILES[name]
    root = Path("runs", "load", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True)
    path = root / "load.json"
    report = {"command": "load", "mode": mode, "profile": name, "workload": profile, "think_time_ms": 100,
              "endpoint": "GET /api/v1/users/me", "source_revision": PINNED, "host_cpu_count": os.cpu_count(),
              "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(), "trials": [], "report": str(path)}
    for workers in ((1, 4) if mode == "compare" else (1,)):
        emit(f"Running {name} with {workers} backend worker(s)...")
        trial = run_trial(Path(repo).resolve(), root, profile, workers, port, java, jar)
        report["trials"].append(trial)
        report["status"] = trial["status"]
        write_json(path, report)
        if trial["status"] == "inconclusive":
            break
    if mode == "compare" and report["status"] == "measured":
        one, four = (trial["metrics"] for trial in report["trials"])
        report["four_minus_one"] = {
            "successful_requests_per_second": round(four["successful_requests_per_second"]
                                                     - one["successful_requests_per_second"], 3),
            "error_pct_points": round(four["error_pct"] - one["error_pct"], 3),
            "p95_ms": four["p95_ms"] - one["p95_ms"] if None not in (one["p95_ms"], four["p95_ms"]) else None,
        }
        write_json(path, report)
    return report
