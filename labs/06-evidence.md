# Phase 6 — Collect evidence from Docker Compose

## What changed

Read `collect_evidence()` in `cloudshield/evidence.py`. It asks Compose which containers belong to the selected project. For each container it records the state, health, exit code, and full container and image IDs. It can also collect a small excerpt of local lab logs. The command is read-only and doesn't start, stop, or repair anything.

The image ID identifies the image Docker is running. A Git commit identifies a source revision. When the backend image has the build label from Phase 1, the collector records that label alongside the image ID. The Judge checks the label against the Git checkout.

## Try it

With Phase 1 running, run this from the `cloudshield` folder:

```powershell
python -m cloudshield evidence --compose ci/compose.yml --env-file .env.phase1.local --project cloudshield-demo --report runs/healthy-evidence.json
```

In the Phase 1 terminal, stop `db`. Run the evidence command again from `cloudshield`:

```powershell
python -m cloudshield evidence --compose ci/compose.yml --env-file .env.phase1.local --project cloudshield-demo --log-lines 10 --report runs/outage-evidence.json
```

Compare the two reports. The backend may still be healthy even though `db` has exited. Run the database-backed Robot check so the report also records what a user actually experiences. Restart `db` using the Phase 1 walkthrough.

Leave `--log-lines` off when you only need status. The lab removes known credentials and common secret-bearing lines from log excerpts. Logs can still contain sensitive values the filter doesn't recognize, so use synthetic data and review excerpts locally before sharing.

## Explain it back

1. How can a backend container stay healthy while its database is unavailable?
2. How would the evidence differ for a stopped database versus an incorrect database hostname?
3. Why is an image ID stronger evidence than a mutable image tag, and what does it still fail to prove about the source?
