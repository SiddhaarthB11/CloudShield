# Phase 15 — Monitor a running deployment

Phase 13 reacts to a save. Phase 14 reacts to GitHub. Phase 15 checks a running Docker Compose deployment on a timer, so it can find a failure even if the Git commit stays the same.

CloudShield collects the running image's Git revision and the Compose service state. It runs the existing Judge, which includes Robot API checks and a comparison of actual database settings against the manifest's expected settings. The monitor then records the outcome:

| Status | What it means |
| --- | --- |
| `healthy` | The deployment and required checks passed. |
| `handoff` | CloudShield recorded a problem that needs attention, such as a stopped database, configuration drift, or a deployment it cannot tie to a known commit. |
| `duplicate` | CloudShield already recorded this failure on the same revision. The report links to the earlier incident. |
| `repair_available` | Gemini produced a reviewed fix and the isolated candidate passed verification. The monitored deployment is still running its original version. |
| `inconclusive` | CloudShield could not complete the observation or cleanup. Read `detail` or the linked report. |

A failure signature uses the deployed revision, check IDs and outcomes, and service states. Polling the same fault does not generate a new repair request every time. A confirmed healthy observation clears the active incidents so CloudShield can recognize a recurrence. Incident state persists across PowerShell sessions in `runs/monitor`.

## 1. Check the healthy Phase 1 deployment

Start the disposable Phase 1 lab with Docker Desktop. From the `cloudshield` folder, run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield monitor examples/target.json --once
```

Expect `healthy` and a `deployed_revision`. If the report is a handoff because the source and image differ, use the pinned Phase 1 checkout and the startup steps in the [Phase 1 lab](01-run-target.md).

## 2. Stop the database without changing Git

Run this only on the Phase 1 lab project. It stops and restarts the existing database container, preserving the lab database volume.

```powershell
$compose = @('--env-file', '.env.phase1.local', '-f', 'ci/compose.yml', '-p', 'cloudshield-demo')
try {
    docker compose @compose stop db
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the lab database.' }
    & ./.venv/Scripts/python.exe -m cloudshield monitor examples/target.json --once
    & ./.venv/Scripts/python.exe -m cloudshield monitor examples/target.json --once
} finally {
    docker compose @compose start db
}
```

Expect `handoff` first and `duplicate` second. The `deployed_revision` stays the same. The first monitor report links to a Judge report and a redacted evidence report. The second points back to the first incident. The CLI returns exit code 1 while the deployment has an unresolved problem.

## 3. Confirm recovery and try a timer

Once Docker shows that the database is healthy, run the first command again. Expect `healthy`. To leave the monitor running, omit `--once`:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield monitor examples/target.json --interval 10
```

It checks immediately, then waits ten seconds after each check. Stop it with **Ctrl+C**. Use one monitor per Compose project.

## Optional — Enable a reviewed repair

Use `--gemini --allow-path backend/app/api/routes/utils.py` when monitoring a deployment with a reproducible, supported API failure. CloudShield checks that the deployment still matches the observed image and revision, then allows one repair attempt for that incident. The candidate runs separately on port `18006`. An unhealthy database is handed off even with Gemini enabled.

## Explain it back

1. How can a deployment break while the Git commit stays the same?
2. How does the monitor avoid asking Gemini about the same outage every ten seconds?
3. Why is a database outage handled differently from a health endpoint returning the wrong value?
