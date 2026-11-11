import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit
from uuid import uuid4

from .candidate import allowed_source_path, validate_candidate_port, write_json
from .checks import read_object
from .controller import repair
from .evidence import collect_evidence
from .fixer import CHECK_EXPECTATIONS
from .incidents import diagnose
from .judge import judge_report, source_revision
from .managed_lab import approved_image, promote_repair, reconcile_config, reconcile_image, require_managed_lab
from .recovery import plan_service_recovery, start_existing_services
from .verifier import identity


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def save_state(path, incidents):
    scratch = path.with_suffix(".tmp")
    write_json(scratch, incidents)
    scratch.replace(path)


def classify(judge, containers):
    services = {row["service"]: row for row in containers}
    for service in ("db", "backend"):
        row = services.get(service, {})
        if row.get("state") != "running" or row.get("health") != "healthy":
            return "handoff", f"The {service} service is missing, stopped, or unhealthy."
    deployed = services["backend"]
    judged = judge.get("deployment")
    if not judged or not deployed.get("source_revision") or identity(deployed) != judged:
        return "handoff", "Could not tie the Judge results to the observed deployment and source revision."
    if judge["overall"] == "pass":
        return "healthy", "The deployment and required checks passed."
    if judge["overall"] != "fail" or any(row["status"] not in {"pass", "fail"} for row in judge["checks"]):
        return "handoff", "The Judge could not establish a confirmed application failure."
    failures = {row["id"] for row in judge["checks"] if row["status"] != "pass"}
    supported = set(CHECK_EXPECTATIONS)
    performance = judge.get("performance", {})
    if performance.get("repair_eligible"):
        supported.add(performance["check_id"])
    if not failures or not failures <= supported:
        return "handoff", "The failure is outside the supported source repair checks."
    return "eligible", "Confirmed a supported runtime failure on the observed revision."


def observe(manifest, target, root, *, proposer, reviewer, allowed, port, judge_runner=None, recover_services=(),
            restore_config=False, apply_code=False, restore_image=False):
    tick = root / uuid4().hex[:12]
    tick.mkdir()
    path = tick / "monitor.json"
    report = {"command": "monitor", "trigger": "scheduled", "project": target[2],
              "observed_at": datetime.now(timezone.utc).isoformat(), "report": str(path)}
    try:
        evidence = collect_evidence(*target)
        containers = evidence["containers"]
        backend = next((row for row in containers if row["service"] == "backend"), {})
        report["deployed_revision"] = backend.get("source_revision")
        judge = (judge_runner or judge_report)(manifest)
        write_json(tick / "judge.json", judge)
        report["judge_report"] = str(tick / "judge.json")
        report["diagnosis"] = diagnose(judge["checks"], containers)
        status, detail = classify(judge, containers)
        wanted_image = approved_image(Path(manifest)) if restore_image else None
        image_recovery = (wanted_image is not None and backend.get("state") == "running"
                          and backend.get("image_id") != wanted_image
                          and any(row["service"] == "db" and (row["state"], row.get("health")) == ("running", "healthy")
                                  for row in containers))
        if image_recovery:
            status, detail = "handoff", "The running backend image differs from the approved image"
            report["diagnosis"] = {"kind": "deployment", "cause": "image_drift", "checks": ["deployment/identity"]}
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
        write_json(path, report)
        return report

    state_path = root / "state.json"
    incidents = read_object(state_path) if state_path.exists() else {}
    failed = sorted((row["id"], row["status"]) for row in judge["checks"] if row["status"] != "pass")
    report.update(status=status, detail=detail, failed_checks=[name for name, _ in failed])
    if status == "healthy":
        if incidents:
            save_state(state_path, {})
        write_json(path, report)
        return report

    service_states = sorted((row["service"], row.get("state") or "", row.get("health") or "") for row in containers)
    key = fingerprint([report["deployed_revision"], failed, service_states, status,
                       backend.get("image_id") if image_recovery else None])
    previous = incidents.get(key)
    plan = plan_service_recovery(containers, recover_services) if recover_services and status == "handoff" else []
    config_recovery = (restore_config and not plan and not image_recovery and report["diagnosis"]["kind"] == "configuration"
                       and bool(judge.get("deployment")))
    can_repair = bool(plan) or config_recovery or image_recovery or (status == "eligible" and proposer is not None)
    if previous and (not can_repair or previous["repair_started"]):
        report.update(status="duplicate", incident=key, duplicate_of=previous["report"])
        write_json(path, report)
        return report

    report["incident"] = key
    try:
        evidence = collect_evidence(*target, log_lines=10)
    except (OSError, ValueError) as exc:
        report["evidence_detail"] = str(exc)
    write_json(tick / "evidence.json", evidence)
    report["evidence_report"] = str(tick / "evidence.json")
    if not can_repair:
        if status == "eligible":
            report.update(status="handoff", detail="Source repair is available when monitoring uses --gemini.")
        write_json(path, report)
        incidents[key] = {"report": str(path), "repair_started": False}
        save_state(state_path, incidents)
        return report

    # Reserve the attempt before making a model call. A restart won't retry the same incident.
    report["status"] = "repair_started"
    write_json(path, report)
    incidents[key] = {"report": str(path), "repair_started": True}
    save_state(state_path, incidents)
    if plan or config_recovery or image_recovery:
        report["actions"] = plan
        try:
            manifest_path = Path(manifest).resolve()
            source = manifest_path.parent / read_object(manifest_path)["source_dir"]
            revision = source_revision(source)
            if not image_recovery and revision != report["deployed_revision"]:
                raise ValueError("Service recovery requires the expected deployed revision")
            if plan:
                start_existing_services(target, plan, revision, lambda: write_json(path, report))
                verify_recovery = judge_runner or judge_report
                for retry in range(3):
                    if retry:
                        sleep(2)
                    verified = verify_recovery(manifest)
                    if verified["overall"] == "pass":
                        break
            elif image_recovery:
                verified = reconcile_image(manifest_path, target, backend, wanted_image, revision, tick,
                                           judge_runner or judge_report)
                report["actions"] = [{"action": "restore_approved_image", "image_id": wanted_image}]
            else:
                verified = reconcile_config(manifest_path, target, judge, tick, judge_runner or judge_report)
                report["actions"] = [{"action": "restore_approved_configuration"}]
            write_json(tick / "recovery-judge.json", verified)
            current = collect_evidence(*target)["containers"]
            recovered = (classify(verified, current)[0] == "healthy"
                         and verified["deployment"]["source_revision"] == revision)
            report.update(status="recovered" if recovered else "handoff",
                          detail="Running deployment passed recovery checks" if recovered
                          else "The action completed but the deployment did not pass recovery checks",
                          recovery_judge=str(tick / "recovery-judge.json"))
            if recovered:
                report["recovered_revision"] = verified["deployment"]["source_revision"]
                save_state(state_path, {})
        except (OSError, ValueError) as exc:
            report.update(status="handoff", detail=str(exc))
        write_json(path, report)
        return report
    result = repair(manifest, None, allowed, port, max_attempts=3, proposer=proposer, reviewer=reviewer,
                    expected_deployment=judge["deployment"], judge=judge_runner)
    report.update(status="repair_available" if result["status"] == "verified" else "handoff",
                  repair_status=result["status"], repair_report=result["report"])
    if any(attempt["cleanup"] == "failed" for attempt in result["attempts"]):
        report.update(status="inconclusive", cleanup="failed")
    if result["status"] == "verified" and apply_code:
        report.update(promote_repair(Path(manifest), target, judge, result, tick, judge_runner or judge_report))
    write_json(path, report)
    if result.get("interrupted") or report.get("interrupted"):
        raise KeyboardInterrupt
    return report


def monitor(manifest_path, emit, *, interval=30, once=False, proposer=None, reviewer=None, allowed=(), port=18006,
            judge_runner=None, recover_services=(), restore_config=False, apply_code=False, restore_image=False):
    if not 5 <= interval <= 3600:
        raise ValueError("Choose an interval from 5 to 3600 seconds")
    manifest_path = Path(manifest_path).resolve()
    manifest = read_object(manifest_path)
    fields = ("compose_file", "credentials_file", "compose_project", "base_url")
    if any(not isinstance(manifest.get(field), str) or not manifest[field].strip() for field in fields):
        raise ValueError("Monitor requires Compose, credentials, project, and URL settings")
    target = ((manifest_path.parent / manifest["compose_file"]).resolve(),
              (manifest_path.parent / manifest["credentials_file"]).resolve(), manifest["compose_project"])
    if recover_services or restore_config or apply_code or restore_image:
        url = urlsplit(manifest["base_url"])
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
            raise ValueError("Automatic recovery requires a loopback lab deployment")
    if restore_config or apply_code or restore_image:
        require_managed_lab(manifest_path, target)
    if apply_code and proposer is None:
        raise ValueError("Automatic deployment requires reviewed Gemini repair")
    if proposer is not None:
        if reviewer is None or not allowed or not all(map(allowed_source_path, allowed)):
            raise ValueError("Scheduled repair requires a Reviewer and explicit allowed application Python paths")
        validate_candidate_port(port)
        if urlsplit(manifest["base_url"]).port == port:
            raise ValueError("Use a different port for the monitored deployment and repair candidate")

    root = Path("runs", "monitor", fingerprint([str(target[0]), target[2]])[:16]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "monitor-lock.sqlite3", timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise ValueError("Could not lock this monitor. Check whether another monitor is running.") from exc
        while True:
            report = observe(manifest_path, target, root, proposer=proposer, reviewer=reviewer, allowed=allowed,
                             port=port, judge_runner=judge_runner, recover_services=recover_services,
                             restore_config=restore_config, apply_code=apply_code, restore_image=restore_image)
            emit(report)
            if once or report.get("cleanup") == "failed":
                return report
            sleep(interval)
