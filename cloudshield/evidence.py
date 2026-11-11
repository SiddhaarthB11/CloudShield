import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit


LAB_SECRET_KEYS = {"APP_SECRET_KEY", "APP_ADMIN_PASSWORD", "POSTGRES_PASSWORD"}
CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
REVISION = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
SECRET_LINE = re.compile(r"(?i)(authorization|cookie|password|passwd|secret|token|api[-_ ]?key|database_url)")


def redact_logs(raw, credentials_file):
    known = {os.environ[key] for key in LAB_SECRET_KEYS if os.environ.get(key)}
    for line in Path(credentials_file).read_text(encoding="utf-8-sig").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in LAB_SECRET_KEYS and value.strip():
            known.add(value.strip().strip("\"'"))
    for value in sorted(known - {""}, key=len, reverse=True):
        raw = raw.replace(value, "[redacted]")
    return ["[sensitive log line omitted]" if SECRET_LINE.search(line) or "://" in line else line[:500]
            for line in raw.splitlines()]


def docker_output(arguments, label, *, env=None, timeout=15):
    try:
        result = subprocess.run(["docker", *arguments], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"Could not run Docker {label}. Check Docker Desktop and retry.") from None
    if result.returncode:
        raise ValueError(f"Docker {label} failed (exit {result.returncode}). Check Docker Desktop and the project inputs.")
    return result.stdout, result.stderr


def compose_rows(output):
    output = output.strip()
    if not output:
        return []
    if output.startswith("["):
        return json.loads(output)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def deployment_config(database_url, secret_ref):
    try:
        url = urlsplit(database_url)
        if url.scheme not in {"postgres", "postgresql", "postgresql+psycopg"} or not url.hostname:
            raise ValueError
        return {"DB_HOST": url.hostname, "DB_PORT": url.port if url.port is not None else 5432,
                "DB_NAME": unquote(url.path.removeprefix("/")), "DB_SECRET_REF": secret_ref}
    except (AttributeError, ValueError):
        raise ValueError("Backend has a missing or invalid database URL") from None


def read_backend_config(container_id):
    template = ('{{range .Config.Env}}{{if eq (index (split . "=") 0) "DATABASE_URL"}}'
                '{{println (json .)}}{{end}}{{end}}'
                '{{json (index .Config.Labels "io.cloudshield.database-secret-ref")}}')
    output, _ = docker_output(["inspect", "--type", "container", "--format", template, container_id], "backend configuration")
    try:
        connection, secret_ref = map(json.loads, output.splitlines())
        if not isinstance(connection, str) or not connection.startswith("DATABASE_URL="):
            raise ValueError
        return deployment_config(connection.partition("=")[2], secret_ref)
    except (TypeError, ValueError):
        raise ValueError("Could not read a safe backend configuration snapshot") from None


def collect_evidence(compose_file, credentials_file, project, log_lines=0):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("Compose project name must use lowercase letters, digits, underscores, or hyphens")
    if not 0 <= log_lines <= 50:
        raise ValueError("Log line count must be between 0 and 50")
    for path in (compose_file, credentials_file):
        if not Path(path).is_file():
            raise ValueError(f"Expected local input file: {path}")
    output, _ = docker_output([
        "compose", "--env-file", str(credentials_file), "-f", str(compose_file), "-p", project,
        "ps", "--all", "--no-trunc", "--format", "json",
    ], "compose ps")
    containers = []
    for row in compose_rows(output):
        if not isinstance(row, dict) or row.get("Project") != project:
            raise ValueError("Docker returned an unexpected project record")
        container_id = row.get("ID", "")
        if not isinstance(container_id, str) or not CONTAINER_ID.fullmatch(container_id):
            raise ValueError("Docker did not report a full container ID")
        image, _ = docker_output(["inspect", "--type", "container", "--format", "{{.Image}}", container_id],
                                 "inspect")
        image_id = image.strip()
        if not IMAGE_ID.fullmatch(image_id):
            raise ValueError("Docker did not report a full image ID")
        container = {"service": row.get("Service"), "state": row.get("State"), "health": row.get("Health"),
                     "exit_code": row.get("ExitCode"), "container_id": container_id, "image_id": image_id}
        if container["service"] == "backend":
            template = '{{json (index .Config.Labels "org.opencontainers.image.revision")}}'
            revision, _ = docker_output(["image", "inspect", "--format", template, image_id], "image revision")
            revision = json.loads(revision)
            container["source_revision"] = revision if isinstance(revision, str) and REVISION.fullmatch(revision) else None
            container["published_ports"] = [port.get("PublishedPort") for port in row.get("Publishers") or []
                                            if port.get("URL") == "127.0.0.1" and port.get("Protocol") == "tcp"
                                            and port.get("TargetPort") == 8000]
        if log_lines:
            stdout, stderr = docker_output(["logs", "--tail", str(log_lines), container_id], "logs")
            container["logs"] = redact_logs(stdout + "\n" + stderr, credentials_file)
        containers.append(container)
    return {"project": project, "observed_at": datetime.now(timezone.utc).isoformat(), "containers": containers}


def running_backend(snapshot):
    matches = [container for container in snapshot["containers"]
               if container["service"] == "backend" and container["state"] == "running"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one running backend in the selected Compose project")
    return matches[0]


def snapshot_config(compose_file, credentials_file, project):
    backend = running_backend(collect_evidence(compose_file, credentials_file, project))
    return read_backend_config(backend["container_id"])
