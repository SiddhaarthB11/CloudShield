"""Local control plane and durable, serial background worker.

The UI submits named operations, never arbitrary commands. Closing a browser or
restarting the HTTP server does not stop the independent worker or its jobs.
"""
import ctypes
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "runs/dashboard/state.sqlite3"
SOURCE_PATHS = ["backend/app/api/routes/utils.py", "backend/app/api/deps.py", "backend/app/api/routes/login.py",
                "backend/app/api/routes/users.py", "backend/app/crud.py"]
ACTIONS = {"judge", "repair", "monitor", "watch", "plant", "fault", "load", "prepare-lab", "start-lab",
           "aws-observe", "aws-recover", "aws-fault"}
SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|authorization|cookie|api.?key|database_url)")


def now():
    return datetime.now(timezone.utc).isoformat()


def sanitize(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if SENSITIVE.search(k) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        if value in {"config/DB_SECRET_REF", "runtime/config/DB_SECRET_REF"}:
            return value  # Check identifiers are evidence, not credential values.
        # Avoid publishing source snippets, credentials, or arbitrary provider output.
        return "\n".join("[sensitive line omitted]" if SENSITIVE.search(line) or re.search(r"://[^ /]+:[^ /]+@", line)
                         else line[:1600] for line in value.splitlines())[:120000]
    return value


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS targets (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, manifest TEXT NOT NULL UNIQUE,
          adapter TEXT NOT NULL, policy TEXT NOT NULL, monitoring INTEGER NOT NULL DEFAULT 0,
          watching INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
          watch_hash TEXT NOT NULL DEFAULT '', watch_due REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, target_id TEXT NOT NULL, action TEXT NOT NULL, options TEXT NOT NULL,
          status TEXT NOT NULL, created TEXT NOT NULL, started TEXT, finished TEXT, pid INTEGER,
          exit_code INTEGER, result TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_target ON jobs(target_id)
          WHERE status IN ('queued','running','blocked');
    """)
    return db


def manifest_path(value):
    path = (ROOT / value).resolve()
    if not any(path.is_relative_to(ROOT / name) for name in ("examples", "runs")) or not path.is_file() or path.suffix != ".json":
        raise ValueError("Choose a target JSON manifest in examples/ or runs/; copy external app manifests into examples/")
    return path


def discover():
    paths = list((ROOT / "examples").glob("*.json"))
    paths += list((ROOT / "runs/candidates").glob("*/target.json"))
    paths += list((ROOT / "runs/aws").glob("*/target.local.json"))
    result = []
    for path in paths:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8-sig"))
            if cfg.get("adapter") == "aws-ecs" or all(k in cfg for k in ("source_dir", "compose_file", "base_url")):
                result.append({"manifest": str(path.relative_to(ROOT)), "adapter": cfg.get("adapter", "docker-compose")})
        except (OSError, ValueError):
            continue
    return result


def policy(data):
    allowed = data.get("allowed_paths", SOURCE_PATHS)
    from .candidate import allowed_source_path
    if not isinstance(allowed, list) or not 1 <= len(allowed) <= 8 or not all(isinstance(p, str) and allowed_source_path(p) for p in allowed):
        raise ValueError("Approve between one and eight explicit application Python files")
    interval = data.get("interval", 30)
    if type(interval) is not int or not 10 <= interval <= 3600:
        raise ValueError("Monitoring interval must be 10–3600 seconds")
    performance = data.get("performance", "")
    if performance not in ("", "normal", "peak"):
        raise ValueError("Choose normal or peak performance checks")
    return {"ai": data.get("ai") is True, "recover_services": data.get("recover_services") is True,
            "managed_recovery": data.get("managed_recovery") is True, "interval": interval,
            "performance": performance, "allowed_paths": allowed}


def register(db, data):
    path = manifest_path(data.get("manifest", ""))
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    adapter = cfg.get("adapter", "docker-compose")
    if adapter == "aws-ecs":
        from .aws_adapter import load_target
        load_target(path)
    elif adapter == "docker-compose":
        if not all(k in cfg for k in ("source_dir", "compose_file", "credentials_file", "base_url")):
            raise ValueError("Incomplete Compose target manifest")
        origin = urlsplit(cfg["base_url"])
        if origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port:
            raise ValueError("The Compose adapter requires a published loopback endpoint")
    else:
        raise ValueError("Supported adapters: docker-compose and aws-ecs")
    name = str(data.get("name", path.stem)).strip()[:80]
    if not name:
        raise ValueError("Target needs a name")
    settings = policy(data)
    if adapter == "docker-compose" and settings["managed_recovery"]:
        from .managed_lab import require_managed_lab
        require_managed_lab(path, ((path.parent / cfg["compose_file"]).resolve(),
                                  (path.parent / cfg["credentials_file"]).resolve(), cfg["compose_project"]))
        if not settings["ai"]:
            raise ValueError("Automatic code promotion requires AI Fixer and Reviewer enabled")
    existing = db.execute("SELECT id FROM targets WHERE manifest=?", (str(path),)).fetchone()
    target_id = existing["id"] if existing else uuid4().hex[:12]
    db.execute("""INSERT INTO targets(id,name,manifest,adapter,policy) VALUES(?,?,?,?,?)
               ON CONFLICT(manifest) DO UPDATE SET name=excluded.name,policy=excluded.policy""",
               (target_id, name, str(path), adapter, json.dumps(settings)))
    db.commit()
    return target_id


def enqueue(db, target_id, action, options=None):
    if action not in ACTIONS:
        raise ValueError("Unknown dashboard operation")
    target = db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
    if target is None:
        raise ValueError("Register a target first")
    if db.execute("SELECT 1 FROM jobs WHERE target_id=? AND status IN ('queued','running','blocked')", (target_id,)).fetchone():
        raise ValueError("This target already has an active job; wait for its verification and cleanup")
    arguments(target, action, options or {})  # validate before storing
    job = uuid4().hex[:12]
    db.execute("INSERT INTO jobs(id,target_id,action,options,status,created) VALUES(?,?,?,?,?,?)",
               (job, target_id, action, json.dumps(options or {}), "queued", now()))
    db.commit()
    return job


def arguments(target, action, options):
    cfg = json.loads(target["policy"])
    manifest = str(manifest_path(target["manifest"]))
    if target["adapter"] == "aws-ecs":
        if action not in {"aws-observe", "aws-recover", "aws-fault"}:
            raise ValueError("Use AWS operations for an ECS target")
        return ["aws", action.removeprefix("aws-"), manifest]
    if action.startswith("aws-"):
        raise ValueError("Select an ECS target")
    ai = ["--gemini"] if cfg["ai"] else []
    allowed = [item for path in cfg["allowed_paths"] for item in ("--allow-path", path)]
    performance = ["--performance", cfg["performance"]] if cfg["performance"] else []
    if action == "judge":
        return ["judge", manifest]
    if action == "repair":
        if not ai:
            raise ValueError("Enable AI repair in the target policy first")
        return ["repair", manifest, "--gemini", "--review", "--max-attempts", "3", *allowed, *performance]
    if action == "monitor":
        result = ["monitor", manifest, "--once", *ai, *(allowed if ai else []), *performance]
        if cfg["recover_services"]:
            result += ["--recover-stopped-service", "db", "--recover-stopped-service", "backend"]
        if cfg["managed_recovery"]:
            result += ["--restore-config", "--restore-image", "--apply-verified-code"]
        return result
    if action == "watch":
        return ["watch", manifest, "--once", "--runtime", *ai, *(allowed if ai else []), *performance]
    # Faults always use independent disposable labs, never alter the selected deployment.
    from .candidate import candidate_target
    _, repo, _, _ = candidate_target(Path(manifest))
    if action == "plant":
        return ["plant", "--repo", str(repo), *ai]
    if action == "fault":
        from .faults import CATALOG
        scenario = options.get("scenario")
        if scenario not in CATALOG:
            raise ValueError("Select a catalog fault")
        return ["fault", "run", scenario, "--repo", str(repo), "--heal",
                *(ai if scenario in {"syntax", "app-logic", "authorization", "login", "latency"} else [])]
    if action == "load":
        if options.get("mode", "normal") not in {"normal", "peak", "stress", "compare"}:
            raise ValueError("Unknown load profile")
        return ["load", options.get("mode", "normal"), "--repo", str(repo)]
    if action == "prepare-lab":
        return ["candidate", "lab", manifest, "--port", "18012"]
    if action == "start-lab":
        if not Path(manifest).is_relative_to(ROOT / "runs/candidates"):
            raise ValueError("Only generated candidate labs can be started from the dashboard")
        project = json.loads(Path(manifest).read_text(encoding="utf-8"))["compose_project"]
        return ["candidate", "start", project]
    raise ValueError("Unknown operation")


def process_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: conservatively assume alive.
        try:
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def detached_options():
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} if os.name == "nt" else {"start_new_session": True}


def start_worker(path):
    # The worker takes a separate SQLite exclusive lock: two servers cannot run two workers.
    with closing(connect(path)) as db:
        db.execute("DELETE FROM meta WHERE key='stop'")
        db.commit()
    return subprocess.Popen([sys.executable, "-m", "cloudshield", "worker", "--database", str(path)],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            **detached_options())


def fingerprint(target):
    from .candidate import candidate_target
    from .watcher import capture
    manifest, repo, _, _ = candidate_target(Path(target["manifest"]))
    return capture(repo, (Path(target["manifest"]).parent / manifest["environment_config"]).resolve())[1]


def schedule(db):
    current = time.time()
    for target in db.execute("SELECT * FROM targets WHERE monitoring=1 OR watching=1").fetchall():
        if db.execute("SELECT 1 FROM jobs WHERE target_id=? AND status IN ('queued','running','blocked')", (target["id"],)).fetchone():
            continue
        try:
            if target["watching"] and target["adapter"] == "docker-compose" and current >= target["watch_due"]:
                mark = fingerprint(target)
                db.execute("UPDATE targets SET watch_due=? WHERE id=?", (current + 3, target["id"]))
                if mark != target["watch_hash"]:
                    enqueue(db, target["id"], "watch")
                    db.execute("UPDATE targets SET watch_hash=? WHERE id=?", (mark, target["id"]))
                    db.commit()
                    continue
            if target["monitoring"] and current >= target["due"]:
                action = "aws-recover" if target["adapter"] == "aws-ecs" else "monitor"
                enqueue(db, target["id"], action)
                db.execute("UPDATE targets SET due=? WHERE id=?", (current + json.loads(target["policy"])["interval"], target["id"]))
            db.commit()
        except (OSError, ValueError):
            # Persist a visible error and pause that target instead of looping forever.
            db.execute("UPDATE targets SET monitoring=0,watching=0 WHERE id=?", (target["id"],))
            db.execute("INSERT INTO jobs(id,target_id,action,options,status,created,finished,detail) VALUES(?,?,?,?,?,?,?,?)",
                       (uuid4().hex[:12], target["id"], "schedule", "{}", "error", now(), now(), "Scheduling failed; check manifest and policy"))
            db.commit()


def run_job(db, job):
    target = db.execute("SELECT * FROM targets WHERE id=?", (job["target_id"],)).fetchone()
    folder = ROOT / "runs/dashboard/jobs" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "output.json"
    code = """import contextlib,json,sys
from pathlib import Path
from cloudshield.__main__ import main
from cloudshield.dashboard import sanitize
import io
capture=io.StringIO()
status=2
def progress_hook(original):
    def save(root,report):
        original(root,report)
        progress={'status':report.get('status'),'stage':report.get('stage'),
                  'report':report.get('report'),'attempts':[
                    {k:v for k,v in attempt.items() if k in ('number','stage','outcome','cleanup')}
                    for attempt in report.get('attempts',[])]}
        destination=Path(sys.argv_output).with_name('progress.json')
        temporary=destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(progress),encoding='utf-8')
        temporary.replace(destination)
    return save
import cloudshield.controller as controller
controller.record=progress_hook(controller.record)
try:
    with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture):
        status=main()
except SystemExit as exc:
    status=exc.code if isinstance(exc.code,int) else 2
except KeyboardInterrupt:
    status=130
except Exception:
    capture.write('Job stopped by an unexpected runner error; inspect the local runner configuration.')
finally:
    text=capture.getvalue()
    try: result=sanitize(json.loads(text))
    except ValueError: result={'detail':sanitize(text[-100000:])}
    destination=Path(sys.argv_output)
    temporary=destination.with_suffix('.tmp')
    temporary.write_text(json.dumps({'exit_code':status,'result':result}),encoding='utf-8')
    temporary.replace(destination)
"""
    try:
        argv = arguments(target, job["action"], json.loads(job["options"]))
        bootstrap = "import sys;sys.argv_output=sys.argv[1];sys.argv=['cloudshield',*sys.argv[2:]];" + "exec(" + repr(code) + ")"
        process = subprocess.Popen([sys.executable, "-c", bootstrap, str(output), *argv], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   **detached_options())
        db.execute("UPDATE jobs SET status='running',started=?,pid=? WHERE id=?", (now(), process.pid, job["id"]))
        db.commit()
        # Keep updating a heartbeat while the independent job runs. Never interrupt a rollback.
        while process.poll() is None:
            db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (str(time.time()),))
            db.commit()
            time.sleep(1)
        finish(db, job["id"], output, process.returncode)
    except (OSError, ValueError):
        db.execute("UPDATE jobs SET status='error',finished=?,detail=? WHERE id=?",
                   (now(), "Job could not start; check its target configuration and dependencies", job["id"]))
        db.commit()


def finish(db, job_id, output, returncode=2):
    try:
        saved = json.loads(output.read_text(encoding="utf-8"))
        result, returncode = saved["result"], saved["exit_code"]
    except (OSError, ValueError, KeyError):
        result = {"detail": "Runner interrupted before its final report; inspect candidate cleanup before retrying"}
    db.execute("UPDATE jobs SET status=?,finished=?,exit_code=?,result=? WHERE id=?",
               ("completed" if returncode == 0 else "attention", now(), returncode, json.dumps(sanitize(result)), job_id))
    row = db.execute("SELECT target_id,action FROM jobs WHERE id=?", (job_id,)).fetchone()
    if (returncode != 0 and not (isinstance(result, dict) and result.get("status") == "duplicate")) or (isinstance(result, dict) and result.get("cleanup") == "failed"):
        db.execute("UPDATE targets SET monitoring=0,watching=0 WHERE id=?", (row["target_id"],))
    elif row["action"] in ("monitor", "aws-recover"):
        target = db.execute("SELECT policy FROM targets WHERE id=?", (row["target_id"],)).fetchone()
        db.execute("UPDATE targets SET due=? WHERE id=?", (time.time() + json.loads(target["policy"])["interval"], row["target_id"]))
    db.commit()


def worker(path=DEFAULT_DB, once=False):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path.with_name("worker-lock.sqlite3"), timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            return
        with closing(connect(path)) as db:
            while True:
                if db.execute("SELECT 1 FROM meta WHERE key='stop'").fetchone():
                    db.execute("DELETE FROM meta WHERE key='heartbeat'")
                    db.commit()
                    return
                db.execute("INSERT OR REPLACE INTO meta VALUES('heartbeat',?)", (str(time.time()),))
                db.commit()
                for row in db.execute("SELECT id,pid FROM jobs WHERE status IN ('running','blocked')").fetchall():
                    if not process_alive(row["pid"]):
                        finish(db, row["id"], ROOT / "runs/dashboard/jobs" / row["id"] / "output.json")
                # A surviving child owns the machine's repair ports even if the worker restarted.
                if not db.execute("SELECT 1 FROM jobs WHERE status IN ('running','blocked')").fetchone():
                    schedule(db)
                    job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                    if job:
                        run_job(db, job)
                if once:
                    return
                time.sleep(1)


def capabilities():
    return {"python": sys.version.split()[0], "docker_cli": bool(shutil.which("docker")),
            "aws_cli": bool(shutil.which("aws")), "java": bool(shutil.which("java")),
            "jmeter": bool(os.environ.get("JMETER_HOME")),
            "gemini_configured": bool(os.environ.get("GEMINI_API_KEY") or (ROOT / ".env.gemini.local").is_file()),
            "note": "Installed/configured indicators; each job validates actual connectivity."}


def state(db):
    targets = []
    for row in db.execute("SELECT * FROM targets ORDER BY name"):
        value = dict(row)
        value["policy"] = json.loads(value["policy"])
        targets.append(value)
    jobs = []
    for row in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100"):
        item = dict(row)
        item["result"] = json.loads(item["result"]) if item["result"] else None
        if item["status"] == "running":
            progress = ROOT / "runs/dashboard/jobs" / item["id"] / "progress.json"
            try:
                item["progress"] = sanitize(json.loads(progress.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                item["progress"] = {"stage": "observing / preparing", "status": "running"}
        jobs.append(item)
    heartbeat = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
    return {"targets": targets, "jobs": jobs, "manifests": discover(), "capabilities": capabilities(),
            "worker_online": bool(heartbeat and time.time() - float(heartbeat[0]) < 10), "source_paths": SOURCE_PATHS}


def serve(port=18700, path=DEFAULT_DB, launch_worker=True):
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a dashboard port from 1024 to 65535")
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{port}"
    with closing(connect(path)):
        pass
    if launch_worker:
        start_worker(path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, data, code=200, content_type="application/json", cookie=False):
            payload = json.dumps(data).encode() if content_type == "application/json" else data
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", f"cloudshield={token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self, mutation=False):
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                return False
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except Exception:
                return False
            session = cookies.get("cloudshield")
            if not session or not secrets.compare_digest(session.value, token):
                return False
            return not mutation or (self.headers.get("Origin") == origin and self.headers.get("X-CloudShield") == "local")

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                return self.send({"error": "Invalid local host"}, 403)
            route = urlsplit(self.path).path
            files = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"), "/app.js": ("app.js", "application/javascript")}
            if route in files:
                name, mime = files[route]
                return self.send((ROOT / "cloudshield/web" / name).read_bytes(), content_type=mime, cookie=route == "/")
            if not self.authorized():
                return self.send({"error": "Open the dashboard first"}, 403)
            if route == "/api/state":
                with closing(connect(path)) as db:
                    return self.send(state(db))
            if route == "/api/report":
                try:
                    name = parse_qs(urlsplit(self.path).query).get("path", [""])[0]
                    report = Path(name).resolve()
                    allowed = {"judge.json", "repair.json", "syntax-repair.json", "monitor.json", "watch.json",
                               "fault.json", "plant.json", "load.json", "trial.json", "aws.json", "review.json",
                               "candidate-judge.json", "baseline.json", "verification.json", "recovery-judge.json"}
                    if not report.is_relative_to(ROOT / "runs") or report.name not in allowed or not report.is_file() or report.stat().st_size > 2000000:
                        raise ValueError("Report is outside the approved evidence files")
                    return self.send(sanitize(json.loads(report.read_text(encoding="utf-8-sig"))))
                except (ValueError, OSError):
                    return self.send({"error": "Report is unavailable or outside approved evidence files"}, 400)
            return self.send({"error": "Unknown endpoint"}, 404)

        def do_POST(self):
            if not self.authorized(True):
                return self.send({"error": "Same-origin local session required"}, 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16000:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                with closing(connect(path)) as db:
                    route = urlsplit(self.path).path
                    if route == "/api/targets":
                        result = {"target_id": register(db, data)}
                    elif route == "/api/jobs":
                        result = {"job_id": enqueue(db, data["target_id"], data["action"], data.get("options"))}
                    elif route == "/api/toggle":
                        target = db.execute("SELECT * FROM targets WHERE id=?", (data["target_id"],)).fetchone()
                        if target is None or data.get("kind") not in ("monitoring", "watching"):
                            raise ValueError("Unknown target or trigger")
                        if data["kind"] == "watching" and target["adapter"] != "docker-compose":
                            raise ValueError("Source watching requires the local Compose adapter")
                        column = data["kind"]  # fixed enum above
                        db.execute(f"UPDATE targets SET {column}=?,due=0,watch_due=0 WHERE id=?", (int(data.get("enabled") is True), target["id"]))
                        db.commit()
                        result = {"status": "updated"}
                    elif route == "/api/cancel":
                        db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=? AND status='queued'", (now(), data["job_id"]))
                        db.commit()
                        result = {"detail": "Queued job cancelled if still queued. Running verification and cleanup finish safely."}
                    elif route == "/api/worker":
                        start_worker(path)
                        result = {"status": "worker_start_requested"}
                    elif route == "/api/pause":
                        db.execute("UPDATE targets SET monitoring=0,watching=0")
                        db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE status='queued'", (now(),))
                        db.execute("INSERT OR REPLACE INTO meta VALUES('stop','1')")
                        db.commit()
                        result = {"detail": "Automation paused. An active job will finish verification and cleanup before the worker exits."}
                    else:
                        return self.send({"error": "Unknown endpoint"}, 404)
                self.send(result)
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                self.send({"error": str(sanitize(str(exc)))[:500]}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"CloudShield dashboard: {origin} (worker persists when the browser closes)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
