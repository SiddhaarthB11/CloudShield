# Phase 1 — Run the separate target

The optional test subject is the official Full Stack FastAPI Template at commit `84c5a9e11a9e58262599e3d772f5386924e02ca7`. CloudShield stays in this repository; the target lives beside it in `../fastapi-target`.

Follow [Getting started](../GETTING_STARTED.md) to create the Python environment. With Docker Desktop running, execute from the repository root:

```powershell
./setup-demo.ps1 -Start
& ./.venv/Scripts/python.exe -m cloudshield judge examples/target.json
```

The script clones/verifies the separate pinned target, generates ignored lab credentials and uses `ci/compose.yml` to build/run it. The backend is published only at `127.0.0.1:18020`; Postgres has no published host port. FastAPI reaches Postgres through the Compose network at `db:5432`.

The two containers demonstrate separate responsibilities: the backend serves requests and the database persists application records. Compose supplies their environment, network, health checks and startup ordering. CloudShield observes them externally.

The credentials remain in `.env.phase1.local`. Keep this file while reusing the database volume; do not commit it or print its contents during an interview. `examples/target.json` records paths and expected deployment settings, not password values.

If the script finds existing target edits or the wrong revision, it stops rather than changing them. A failed setup is not proof that CloudShield repaired the app; inspect the reported prerequisite first.
