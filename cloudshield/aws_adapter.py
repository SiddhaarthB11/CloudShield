"""ECS adapter. Credentials stay in the AWS credential chain, never in reports.

This adapter restores a previously enrolled task definition; it does not give an
LLM a shell or permission to invent infrastructure changes.
"""
import copy
import ipaddress
import json
import os
import re
import subprocess
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from .checks import read_object

ROOT = Path(__file__).resolve().parent.parent
ARN = re.compile(r"arn:aws:ecs:([a-z0-9-]+):(\d{12}):(cluster|service|task-definition|task)/[A-Za-z0-9_:/.-]+\Z")


def aws(config, service, operation, *arguments):
    command = ["aws", "--region", config["region"], "--output", "json", "--no-cli-pager"]
    if config.get("profile"):
        command += ["--profile", config["profile"]]
    try:
        run = subprocess.run(command + [service, operation, *arguments], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=70,
                             env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"})
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("AWS CLI unavailable or timed out; check installation, login and connectivity") from None
    if run.returncode:
        code = re.search(r"\(([A-Za-z0-9_.]+)\)", run.stderr)
        raise ValueError(f"AWS {service}/{operation} failed: {code[1] if code else 'check profile permissions and connectivity'}")
    return json.loads(run.stdout or "{}")


def load_target(path):
    config = read_object(Path(path))
    region = config.get("region")
    if config.get("adapter") != "aws-ecs" or not isinstance(region, str) or not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
        raise ValueError("Expected an aws-ecs manifest with an explicit AWS region")
    profile = config.get("profile", "default")
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", profile):
        raise ValueError("Invalid AWS profile name")
    account = None
    for key, kind in (("cluster", "cluster"), ("service", "service"), ("approved_task_definition", "task-definition")):
        value = config.get(key)
        match = ARN.fullmatch(value) if isinstance(value, str) else None
        if not match or match[1] != config["region"] or match[3] != kind or (account and match[2] != account):
            raise ValueError("AWS resources must be full ARNs in the same region and account")
        account = match[2]
    digest = config.get("image_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Pin the approved backend image digest")
    if type(config.get("desired_count")) is not int or config["desired_count"] != 1:
        raise ValueError("This sandbox adapter supports exactly one task")
    for key in ("sandbox", "allow_restore", "probe_health", "synthetic_login", "cloudwatch"):
        if key in config and type(config[key]) is not bool:
            raise ValueError(f"{key} must be a JSON boolean")
    return config


def enroll(stack, profile, region, destination):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack):
        raise ValueError("Invalid stack name")
    cfg = {"profile": profile, "region": region}
    outputs = aws(cfg, "cloudformation", "describe-stacks", "--stack-name", stack)["Stacks"][0]["Outputs"]
    values = {row["OutputKey"]: row["OutputValue"] for row in outputs}
    cfg.update(adapter="aws-ecs", name=stack, stack=stack, cluster=values["Cluster"], service=values["Service"],
               approved_task_definition=values["ApprovedTaskDefinition"], image_digest=values["ImageUri"].split("@")[-1],
               log_group=values["LogGroup"], admin_secret=values["AdminSecret"], desired_count=1,
               source_revision=values["SourceRevision"], sandbox=True, allow_restore=False,
               probe_health=True, synthetic_login=False)
    cfg["cloudwatch"] = True
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Enrollment never overwrites an approved baseline; use a new manifest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    load_target(destination)
    return {"status": "enrolled", "manifest": str(destination), "restore_enabled": False}


def describe(config, call=aws):
    result = call(config, "ecs", "describe-services", "--cluster", config["cluster"], "--services", config["service"])
    services = result.get("services", [])
    if result.get("failures") or len(services) != 1 or services[0].get("status") != "ACTIVE":
        raise ValueError("The registered ECS service is unavailable")
    service = services[0]
    if service["serviceArn"] != config["service"] or service["clusterArn"] != config["cluster"]:
        raise ValueError("AWS returned a different service identity")
    task_ids = call(config, "ecs", "list-tasks", "--cluster", config["cluster"], "--service-name", config["service"],
                    "--desired-status", "RUNNING").get("taskArns", [])
    tasks = []
    if task_ids:
        data = call(config, "ecs", "describe-tasks", "--cluster", config["cluster"], "--tasks", *task_ids)
        if data.get("failures"):
            raise ValueError("Could not identify all running tasks")
        tasks = data.get("tasks", [])
    return service, tasks


def endpoint(config, task, call):
    eni = next((d["value"] for a in task.get("attachments", []) for d in a.get("details", [])
                if d["name"] == "networkInterfaceId"), None)
    if not eni or not re.fullmatch(r"eni-[a-f0-9]+", eni):
        raise ValueError("No task network interface is available")
    rows = call(config, "ec2", "describe-network-interfaces", "--network-interface-ids", eni)["NetworkInterfaces"]
    address = rows[0].get("Association", {}).get("PublicIp", "")
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("Sandbox task has no public IPv4 address")
    return address


def request(address, method, path, body=None, headers=None):
    connection = HTTPConnection(address, 8000, timeout=6)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("Probe response exceeded its size limit")
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        return response.status, data
    finally:
        connection.close()


def probe(config, task, call):
    address = endpoint(config, task, call)
    status, body = request(address, "GET", "/api/v1/utils/health-check/")
    checks = [{"id": "aws/api-health", "status": "pass" if status == 200 and body is True else "fail"}]
    status, _ = request(address, "GET", "/api/v1/users/me")
    checks.append({"id": "aws/anonymous-denied", "status": "pass" if status in (401, 403) else "fail"})
    if config.get("synthetic_login"):
        if not config.get("sandbox"):
            raise ValueError("Plain HTTP synthetic login is limited to this disposable sandbox")
        secret = config.get("admin_secret", "")
        if not secret.startswith(f"arn:aws:secretsmanager:{config['region']}:{ARN.fullmatch(config['cluster'])[2]}:secret:"):
            raise ValueError("Synthetic credential must belong to the registered account and region")
        password = call(config, "secretsmanager", "get-secret-value", "--secret-id", secret)["SecretString"]
        status, body = request(address, "POST", "/api/v1/login/access-token",
                               urlencode({"username": "admin@example.com", "password": password}),
                               {"Content-Type": "application/x-www-form-urlencoded"})
        token = body.get("access_token") if isinstance(body, dict) else None
        ok = status == 200 and isinstance(token, str) and bool(token)
        if ok:
            status, body = request(address, "GET", "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
            ok = status == 200 and isinstance(body, dict) and body.get("is_active") is True
        checks.append({"id": "aws/database-backed-login", "status": "pass" if ok else "fail"})
    return checks


def observe(config, call=aws):
    service, tasks = describe(config, call)
    checks = []
    def check(name, ok):
        checks.append({"id": f"aws/{name}", "status": "pass" if ok else "fail"})
    check("approved-task-definition", service["taskDefinition"] == config["approved_task_definition"])
    check("desired-count", service["desiredCount"] == config["desired_count"])
    check("running-task", len(tasks) == config["desired_count"] and service.get("pendingCount", 0) == 0)
    for task in tasks:
        backend = next((c for c in task.get("containers", []) if c["name"] == "backend"), {})
        check("image-digest", backend.get("imageDigest") == config["image_digest"])
        check("task-health", task.get("lastStatus") == "RUNNING" and task.get("healthStatus") == "HEALTHY")
        check("task-revision", task.get("taskDefinitionArn") == config["approved_task_definition"])
    if len(tasks) == 1 and config.get("probe_health", True):
        try:
            checks.extend(probe(config, tasks[0], call))
        except (OSError, ValueError, HTTPException):
            checks.append({"id": "aws/probe-connectivity", "status": "error",
                           "detail": "Probe could not complete; check client CIDR, task readiness and access"})
    report = {"command": "aws", "adapter": "aws-ecs", "status": "healthy" if all(c["status"] == "pass" for c in checks) else "fail",
            "verification_scope": "infrastructure-and-api" if config.get("probe_health", True) else "infrastructure-only",
            "checks": checks, "task_definition": service["taskDefinition"], "desired_count": service["desiredCount"],
            "tasks": [{"arn": t["taskArn"], "status": t.get("lastStatus"), "health": t.get("healthStatus")} for t in tasks]}
    if config.get("cloudwatch") and config.get("log_group"):
        try:
            counts = {"events": 0, "error_lines": 0, "timeout_lines": 0}
            for task in tasks[:2]:
                stream = "api/backend/" + task["taskArn"].split("/")[-1]
                events = call(config, "logs", "get-log-events", "--log-group-name", config["log_group"],
                              "--log-stream-name", stream, "--limit", "50").get("events", [])
                counts["events"] += len(events)
                counts["error_lines"] += sum(bool(re.search(r"(?i)error|exception", e.get("message", ""))) for e in events)
                counts["timeout_lines"] += sum("timeout" in e.get("message", "").lower() for e in events)
            report["cloudwatch"] = {"status": "collected", **counts, "detail": "Aggregate counts only; raw log messages are not persisted"}
        except (ValueError, KeyError):
            report["cloudwatch"] = {"status": "unavailable", "detail": "Check log group permissions and task log readiness"}
    return report


def recover(config, call=aws, wait=time.sleep, attempts=36):
    before = observe(config, call)
    if before["status"] == "healthy":
        return {**before, "action": "none"}
    if not config.get("allow_restore"):
        return {**before, "status": "handoff", "detail": "Approved task restoration is disabled in this target policy"}
    drift = before["task_definition"] != config["approved_task_definition"] or before["desired_count"] != config["desired_count"]
    if not drift:
        return {**before, "status": "handoff", "detail": "No approved deployment drift to restore; ECS handles task replacement"}
    # Re-read immediately before changing the service. Never overwrite a newer deployment.
    current, _ = describe(config, call)
    if (current["taskDefinition"], current["desiredCount"]) != (before["task_definition"], before["desired_count"]):
        return {**before, "status": "handoff", "detail": "Service changed during diagnosis; no action taken"}
    call(config, "ecs", "update-service", "--cluster", config["cluster"], "--service", config["service"],
         "--task-definition", config["approved_task_definition"], "--desired-count", str(config["desired_count"]))
    after = before
    for _ in range(attempts):
        wait(10)
        after = observe(config, call)
        if after["status"] == "healthy":
            return {**after, "status": "recovered", "action": "restore_approved_task_definition", "before": before}
        if after["task_definition"] != config["approved_task_definition"]:
            return {**after, "status": "handoff", "detail": "Another deployment superseded recovery; stopped"}
    return {**after, "status": "handoff", "action": "restore_approved_task_definition",
            "detail": "Restoration requested but recovery verification did not pass"}


def fault(config, call=aws):
    if not config.get("sandbox") or not config.get("allow_restore"):
        raise ValueError("Fault injection requires an explicitly authorized disposable sandbox")
    if observe(config, call)["status"] != "healthy":
        raise ValueError("Start the AWS fault lab from a healthy approved baseline")
    definition = call(config, "ecs", "describe-task-definition", "--task-definition", config["approved_task_definition"])["taskDefinition"]
    keys = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
            "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform", "ephemeralStorage")
    changed = copy.deepcopy({k: definition[k] for k in keys if k in definition})
    backend = next(c for c in changed["containerDefinitions"] if c["name"] == "backend")
    env = next((e for e in backend.get("environment", []) if e["name"] == "DB_HOST"), None)
    if env is None or env["value"] != "127.0.0.1":
        raise ValueError("This lab only injects DB_HOST drift into the supplied sandbox task")
    env["value"] = "cloudshield-missing-db"
    registered = call(config, "ecs", "register-task-definition", "--cli-input-json", json.dumps(changed))["taskDefinition"]["taskDefinitionArn"]
    current, _ = describe(config, call)
    if current["taskDefinition"] != config["approved_task_definition"]:
        raise ValueError("Service changed during fault preparation; fault was not deployed")
    call(config, "ecs", "update-service", "--cluster", config["cluster"], "--service", config["service"], "--task-definition", registered)
    return {"status": "fault_requested", "fault": "DB_HOST drift", "task_definition": registered}


def execute(action, path):
    config = load_target(path)
    report = {"observe": observe, "recover": recover, "fault": fault}[action](config)
    root = ROOT / "runs" / "aws" / uuid4().hex[:12]
    root.mkdir(parents=True)
    report["report"] = str(root / "aws.json")
    Path(report["report"]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
