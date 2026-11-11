# Phase 22 — Restart an authorized stopped service

The monitor can now start an existing stopped container after checking the project, container ID, and expected source revision. It waits for Docker health, then reruns the app checks. The same database volume stays attached.

## Run the isolated exercise

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run dependency --heal
```

Expect `matched` and `recovered`. In the report folder, inspect the failed Judge result, `actions.json`, and `recovery-judge.json`. The runtime action is a Docker start of the recorded container. A Gemini call is unnecessary when the container state identifies the exact approved action.

## Enable it in the monitor

Run this against the disposable Phase 1 deployment before stopping its database in another terminal. Stop the monitor with Ctrl+C when done.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield monitor examples/target.json --recover-stopped-service db --interval 10
```

Expect `recovered` after the database restarts and login passes. The option can also be repeated with `backend` for a known stopped backend. The monitor hands off missing, unhealthy-but-running, and ambiguous containers.

**Explain it back.** What proves that Docker starting a container actually restored the app?
