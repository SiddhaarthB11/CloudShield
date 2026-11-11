import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

from .candidate import (allowed_source_path, candidate_deployment, candidate_target, commit_candidate,
                        create_candidate_worktree, git, run_candidate, validate_candidate_port, write_json)
from .checks import check, check_python, read_object, summarize_checks, validate_config
from .controller import repair
from .incidents import diagnose
from .judge import judge_report, source_revision
from .syntax_repair import repair_syntax, syntax_paths


APP = Path("backend", "app")


def capture(repo, config_path):
    head = git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
    base_paths = git(repo, "ls-tree", "-r", "-z", "--name-only", head, "--", APP.as_posix())
    local_paths = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", APP.as_posix())
    files = {}
    for name in sorted(set((base_paths + local_paths).split(b"\0")) - {b""}):
        relative = PurePosixPath(os.fsdecode(name))
        if relative.suffix != ".py":
            continue
        path = repo / relative
        if path.resolve() != path:
            raise ValueError(f"Local watch requires ordinary Python files: {relative}")
        try:
            if path.exists() and not path.is_file():
                raise ValueError(f"Local watch requires ordinary Python files: {relative}")
            files[relative.as_posix()] = path.read_bytes()
        except FileNotFoundError:
            files[relative.as_posix()] = None
    config = config_path.read_bytes()
    hashes = {name: hashlib.sha256(content).hexdigest() if content is not None else None
              for name, content in files.items()}
    fingerprint = hashlib.sha256(json.dumps([head, hashlib.sha256(config).hexdigest(), hashes]).encode()).hexdigest()
    return head, fingerprint, files, config


def write_snapshot(directory, files):
    for name, content in files.items():
        path = directory / name
        if path.resolve() != path:
            raise ValueError(f"Snapshot path is not an ordinary file: {name}")
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def static_report(root, files, config, python_version):
    with TemporaryDirectory(dir=root) as scratch:
        directory = Path(scratch)
        write_snapshot(directory, files)
        config_path = directory / "config.json"
        config_path.write_bytes(config)
        try:
            results = check_python(directory / APP, python_version)
            try:
                results.extend(validate_config(read_object(config_path)))
            except ValueError as exc:
                results.append(check("config/input", "fail", str(exc)))
        except (OSError, ValueError) as exc:
            results = [check("runner/input", "error", str(exc))]
    return summarize_checks("static", results)


def check_snapshot(target, snapshot, *, runtime_port=None, repair_port=None, allowed=(), proposer=None, reviewer=None,
                   judge_runner=None):
    manifest, repo, compose_file, robot_suite = target
    head, fingerprint, files, config = snapshot
    root = Path("runs", "watch", uuid4().hex[:12]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    report_path = root / "watch.json"
    report = {"command": "watch", "trigger": "local_save", "status": "running", "source_head": head,
              "snapshot_sha256": fingerprint, "report": str(report_path)}
    project = None
    try:
        static = static_report(root, files, config, manifest["python_version"])
        write_json(root / "static.json", static)
        report.update(static_report=str(root / "static.json"), status=static["overall"],
                      diagnosis=diagnose(static["checks"]))
        can_fix_syntax = proposer is not None and bool(syntax_paths(static["checks"]))
        if runtime_port is None or (static["overall"] != "pass" and not can_fix_syntax):
            return report

        sandbox = create_candidate_worktree(repo, head)
        source = sandbox / "source"
        write_snapshot(source, files)
        git(source, "add", "-A", "--", APP.as_posix())
        changed = git(source, "diff", "--cached", "--name-only")
        revision = commit_candidate(source, "CloudShield local snapshot") if changed else source_revision(source)
        if can_fix_syntax:
            result = repair_syntax(target, revision, static, allowed, repair_port, proposer, reviewer, judge_runner)
            report.update(status=result["status"], syntax_repair_report=result["report"], snapshot_revision=revision)
            if result.get("cleanup") == "failed":
                report["repair_cleanup_failed"] = True
            return report
        target_snapshot = candidate_deployment(sandbox, manifest, compose_file, robot_suite, head, revision,
                                               runtime_port, snapshot_sha256=fingerprint)
        project = target_snapshot["project"]
        report.update(project=project, snapshot_revision=revision, status="running")
        write_json(report_path, report)
        run_candidate(project, "start")
        if proposer is not None:
            result = repair(target_snapshot["manifest"], None, allowed, repair_port, max_attempts=3,
                            proposer=proposer, reviewer=reviewer, judge=judge_runner)
            report.update(status=result["status"], repair_report=result["report"])
            if any(attempt["cleanup"] == "failed" for attempt in result["attempts"]):
                report["repair_cleanup_failed"] = True
        else:
            judged = (judge_runner or judge_report)(target_snapshot["manifest"])
            write_json(root / "judge.json", judged)
            report.update(status=judged["overall"], judge_report=str(root / "judge.json"),
                          diagnosis=diagnose(judged["checks"]))
    except (OSError, ValueError) as exc:
        report.update(status="inconclusive", detail=str(exc))
    except KeyboardInterrupt:
        report.update(status="inconclusive", detail="Watch interrupted")
        raise
    finally:
        if project is not None:
            try:
                run_candidate(project, "discard")
            except (OSError, ValueError) as exc:
                report.update(status="inconclusive", cleanup="failed", cleanup_detail=str(exc))
            else:
                report["cleanup"] = "discarded"
        write_json(report_path, report)
    return report


def watch(manifest_path, emit, *, debounce=1.0, once=False, runtime_port=None, repair_port=None,
          allowed=(), proposer=None, reviewer=None, judge_runner=None):
    if not 0.1 <= debounce <= 30:
        raise ValueError("Choose a debounce from 0.1 to 30 seconds")
    manifest_path = Path(manifest_path).resolve()
    target = candidate_target(manifest_path)
    manifest, repo, _, _ = target
    for key in ("environment_config", "python_version", "base_url"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Manifest requires a nonempty {key}")
    config_path = (manifest_path.parent / manifest["environment_config"]).resolve()
    ports = {urlsplit(manifest["base_url"]).port}
    for port in (runtime_port, repair_port):
        if port is not None:
            validate_candidate_port(port)
            if port in ports:
                raise ValueError("Use different ports for the original app, watch snapshot, and repair candidate")
            ports.add(port)
    if proposer is not None and (runtime_port is None or repair_port is None or not allowed or reviewer is None):
        raise ValueError("AI watch requires runtime testing, distinct ports, a Reviewer, and allowed paths")
    if not all(allowed_source_path(name) for name in allowed):
        raise ValueError("Allow only explicit Python files under backend/app outside tests")

    pending = completed = None
    while True:
        snapshot = capture(repo, config_path)
        fingerprint = snapshot[1]
        now = monotonic()
        if fingerprint != pending:
            pending, changed_at = fingerprint, now
        if fingerprint != completed and now - changed_at >= debounce:
            report = check_snapshot(target, snapshot, runtime_port=runtime_port, repair_port=repair_port,
                                    allowed=allowed, proposer=proposer, reviewer=reviewer, judge_runner=judge_runner)
            emit(report)
            if once or report.get("cleanup") == "failed" or report.get("repair_cleanup_failed"):
                return report
            completed = fingerprint
        sleep(min(0.25, debounce))
