import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .checks import read_object
from .evidence import redact_logs
from .judge import source_revision, verified_backend


PROJECT = re.compile(r"cloudshield-candidate-[0-9a-f]{12}\Z")


def git(directory, *args, patch=None):
    command = ["git", "-C", str(directory), "-c", "core.hooksPath=/dev/null",
               "-c", "core.fsmonitor=false", "-c", "core.longpaths=true", *args]
    try:
        result = subprocess.run(command, input=patch, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"Could not run Git {args[0]} for the candidate") from None
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed for the candidate (exit {result.returncode})")
    return result.stdout


def candidate_diff(directory, baseline_revision, candidate_revision):
    return git(directory, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
               baseline_revision, candidate_revision, "--")


def allowed_source_path(name):
    path = PurePosixPath(name)
    return (path.as_posix() == name and path.parts[:2] == ("backend", "app")
            and path.suffix == ".py" and ".." not in path.parts and "\\" not in name
            and ":" not in name and "tests" not in path.parts)


def patch_files(directory, patch, allowed):
    if not allowed or not all(allowed_source_path(name) for name in allowed):
        raise ValueError("Allow only explicit Python files under backend/app outside tests")
    rows = git(directory, "apply", "--numstat", "-z", "-", patch=patch).split(b"\0")
    paths = set()
    for row in rows[:-1]:
        added, deleted, name = row.decode("utf-8").split("\t", 2)
        if not added.isdecimal() or not deleted.isdecimal() or name not in allowed:
            raise ValueError("Patch contains a binary, rename, or unapproved file change")
        tree = git(directory, "ls-tree", "HEAD", "--", name)
        if not tree.startswith((b"100644 blob ", b"100755 blob ")):
            raise ValueError("Patch must modify a tracked regular file")
        paths.add(name)
    if not paths:
        raise ValueError("Patch has no approved file changes")
    if git(directory, "apply", "--summary", "-", patch=patch).strip():
        raise ValueError("Patch must preserve file names and modes")
    return paths


def write_json(path, content):
    path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")


def read_patch(path):
    with Path(path).open("rb") as stream:
        patch = stream.read(65_537)
    if not patch or len(patch) > 65_536:
        raise ValueError("Provide a nonempty patch no larger than 64 KiB")
    return patch


def validate_candidate_port(port):
    if not 1024 <= port <= 65535 or port in {18000, 18001}:
        raise ValueError("Choose a candidate port from 1024–65535 other than 18000 or 18001")


def candidate_target(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_object(manifest_path)

    def resolve_field(key):
        value = manifest.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Manifest requires {key}")
        return (manifest_path.parent / value).resolve()

    source_dir = resolve_field("source_dir")
    compose_file = resolve_field("compose_file")
    robot_suite = resolve_field("robot_suite")
    if not source_dir.is_dir() or not compose_file.is_file() or not robot_suite.is_file():
        raise ValueError("Expected the target source, Compose file, and trusted Robot suite")
    repo = Path(os.fsdecode(git(source_dir, "rev-parse", "--show-toplevel").strip())).resolve()
    if source_dir != repo / "backend" / "app" or not (repo / "backend" / "Dockerfile").is_file():
        raise ValueError("Phase 7 expects the FastAPI lab checkout with backend/app and backend/Dockerfile")
    return manifest, repo, compose_file, robot_suite


def create_candidate_worktree(repo, revision):
    root = Path("runs", "candidates", "cloudshield-candidate-" + uuid4().hex[:12]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    git(repo, "worktree", "add", "--detach", str(root / "source"), revision)
    return root


def commit_candidate(worktree, message):
    git(worktree, "-c", "user.name=CloudShield lab", "-c", "user.email=cloudshield@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", message)
    return source_revision(worktree)


def prepare_candidate(manifest_path, patch_path, allowed, port, *, expected_revision=None):
    validate_candidate_port(port)
    manifest, repo, compose_file, robot_suite = candidate_target(manifest_path)
    revision = source_revision(repo)
    if expected_revision is not None and revision != expected_revision:
        raise ValueError("The source changed after the baseline run. Run the Judge again.")
    patch = read_patch(patch_path)
    paths = patch_files(repo, patch, set(allowed))
    root = create_candidate_worktree(repo, revision)
    worktree = root / "source"
    git(worktree, "apply", "--index", "--check", "-", patch=patch)
    git(worktree, "apply", "--index", "-", patch=patch)
    changed = git(worktree, "diff", "--cached", "--name-only", "-z").decode().rstrip("\0").split("\0")
    if set(changed) != paths or git(worktree, "diff", "--cached", "--summary").strip():
        raise ValueError(f"Patch changed unexpected paths or file metadata. Inspect {worktree}")
    candidate_revision = commit_candidate(worktree, "CloudShield candidate")
    return candidate_deployment(root, manifest, compose_file, robot_suite, revision, candidate_revision, port,
                                patch_sha256=hashlib.sha256(patch).hexdigest(), paths=sorted(paths))


def prepare_clean_lab(manifest_path, port):
    validate_candidate_port(port)
    manifest, repo, compose_file, robot_suite = candidate_target(manifest_path)
    revision = source_revision(repo)
    root = create_candidate_worktree(repo, revision)
    return candidate_deployment(root, manifest, compose_file, robot_suite, revision, revision, port)


def candidate_deployment(root, manifest, compose_file, robot_suite, revision, candidate_revision, port, **details):
    project = root.name
    worktree = root / "source"
    credentials = root / "candidate.env"
    variables = {"POSTGRES_PASSWORD": secrets.token_hex(24), "APP_SECRET_KEY": secrets.token_hex(32),
                 "APP_ADMIN_PASSWORD": secrets.token_hex(24), "APP_HOST_PORT": str(port),
                 "APP_DB_HOST": "db", "APP_DB_SECRET_REF": f"{project}/postgres",
                 "APP_SOURCE_REVISION": candidate_revision}
    context = worktree.as_posix().replace("'", "\\'")
    credentials.write_text("".join(f"{key}={value}\n" for key, value in variables.items()) +
                           f"APP_BUILD_CONTEXT='{context}'\n", encoding="utf-8")
    config = {"DB_HOST": "db", "DB_PORT": 5432, "DB_NAME": "app", "DB_SECRET_REF": variables["APP_DB_SECRET_REF"]}
    write_json(root / "expected.json", config)
    candidate_manifest = root / "target.json"
    write_json(candidate_manifest, {**manifest, "source_dir": "source/backend/app", "compose_file": str(compose_file),
                                    "compose_project": project, "base_url": f"http://127.0.0.1:{port}",
                                    "environment_config": "expected.json", "credentials_file": "candidate.env",
                                    "robot_suite": str(robot_suite)})
    summary = {"project": project, "baseline_revision": revision, "candidate_revision": candidate_revision,
               "manifest": str(candidate_manifest), "worktree": str(worktree), "port": port, **details}
    write_json(root / "candidate.json", summary)
    return summary


def run_candidate(project, action):
    if action not in {"start", "stop", "discard"} or not PROJECT.fullmatch(project):
        raise ValueError("Use a candidate project ID printed by candidate prepare")
    parent = Path("runs", "candidates").resolve()
    root = (parent / project).resolve()
    if root.parent != parent or root.name != project:
        raise ValueError("Candidate directory is outside the expected workspace")
    summary = read_object(root / "candidate.json")
    manifest = read_object(root / "target.json")
    if summary.get("project") != project or manifest.get("compose_project") != project:
        raise ValueError("Candidate project metadata does not match")
    compose_file = Path(manifest.get("compose_file", ""))
    port = summary.get("port")
    if (not compose_file.is_file() or type(port) is not int or not 1024 <= port <= 65535
            or port in {18000, 18001} or manifest.get("base_url") != f"http://127.0.0.1:{port}"):
        raise ValueError("Candidate Compose path or port metadata is invalid")
    credentials = root / "candidate.env"
    compose = ["docker", "compose", "--env-file", str(credentials), "-f", str(compose_file), "-p", project]
    # Compose gives shell variables priority over its env file. Remove these so a
    # Phase 1 or 2 terminal cannot supply the candidate's port, build path or credentials.
    candidate_keys = {line.partition("=")[0] for line in credentials.read_text(encoding="utf-8").splitlines()}
    env = {key: value for key, value in os.environ.items() if key.upper() not in candidate_keys}

    def docker(*args):
        try:
            result = subprocess.run([*compose, *args], env=env, timeout=600, check=False, capture_output=True,
                                    encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError(f"Docker Compose {args[0]} was unavailable or timed out for {project}") from None
        if result.returncode:
            tail = "\n".join(redact_logs(result.stdout + "\n" + result.stderr, credentials)[-8:]).strip()
            detail = f"\n{tail}" if tail else ""
            raise ValueError(f"Docker Compose {args[0]} failed for {project} (exit {result.returncode}).{detail}")

    if action == "stop":
        docker("stop")
        return {"project": project, "status": "stopped"}
    if action == "discard":
        docker("down", "--volumes")
        return {"project": project, "status": "deployment discarded", "worktree": str(root / "source")}

    if manifest.get("managed_release"):
        raise ValueError("This managed lab was promoted. Use monitored service recovery to restart its existing containers.")
    revision = source_revision(root / "source")
    if revision != summary["candidate_revision"]:
        raise ValueError("Candidate source changed since preparation. Prepare a fresh candidate.")
    env.update(APP_BUILD_CONTEXT=str(root / "source"), APP_SOURCE_REVISION=revision, APP_HOST_PORT=str(port),
               APP_DB_HOST="db", APP_DB_SECRET_REF=f"{project}/postgres")
    docker("build", "backend")
    docker("up", "-d", "--wait", "--wait-timeout", "90", "db")
    docker("run", "--rm", "--no-deps", "backend", "bash", "scripts/prestart.sh")
    docker("up", "-d", "--wait", "--wait-timeout", "90", "backend")
    deployment = verified_backend(compose_file, credentials, project, manifest["base_url"], revision)
    report = {**summary, "status": "started", "deployment": deployment}
    write_json(root / "deployment.json", report)
    return report
