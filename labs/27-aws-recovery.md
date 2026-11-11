# Phase 27 — Observe and restore the approved AWS deployment

## What this part achieves

The ECS adapter checks the service's task definition, desired count, task count/readiness, backend digest, task health and task revision. When enabled, it also checks the health API, anonymous access denial and synthetic database-backed login. Its report distinguishes a failed check from a probe that could not complete. AWS CLI errors become bounded diagnostics without dumping provider output or credentials.

For an authorized deployment drift, CloudShield requests the previously approved task definition and desired count, then polls the replacement tasks and repeats the checks. A successful API call is not recovery: `recovered` requires the recorded checks to pass. If the service changes during diagnosis, restoration stops. A permission failure, unknown code bug, network timeout or exhausted verification becomes an error/handoff instead of invented success.

ECS already replaces failed tasks. CloudShield adds application checks and restoration of an approved deployment configuration; it does not claim credit for the ECS scheduler's ordinary restarts. This adapter does not autonomously write and deploy new AWS application code. Reviewed source repair currently uses the local Docker candidate pipeline; cloud release of a newly fixed image requires an app-specific release integration.

## Lab

First inspect the real registered service:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield aws observe runs/aws/cloudshield-lab/target.local.json
```

In that disposable sandbox manifest set `allow_restore` and `synthetic_login` to `true`. Keep the approved task definition and image digest unchanged. The policy grants restoration of that known baseline, not arbitrary changes suggested by a model.

Run observation again. It must be healthy before planting a fault. Then:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield aws fault runs/aws/cloudshield-lab/target.local.json
```

This copies the approved task definition and changes only the backend's DB_HOST to a nonexistent host. It registers and deploys that revision. Wait for AWS to replace the task, then observe. The new task's shallow health endpoint may still work while database-backed login fails. Task-definition drift should also be visible. The original manifest remains the approved baseline.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield aws recover runs/aws/cloudshield-lab/target.local.json
```

Inspect `runs/aws/<run-id>/aws.json`. It should show the exact approved definition and digest, passing probes and `status: recovered`. If AWS's own rollback restores it before CloudShield acts, the correct result is `healthy` with no action, not a CloudShield recovery claim. If the result is handoff, read the failed check IDs and your AWS service events; do not relabel it as success.

The dashboard in phase 28 can run these commands and continuously schedule `aws recover` observations. Healthy checks do not mutate the service. Unknown failures pause dashboard automation for inspection rather than repeating unsafe actions indefinitely.

## Guard boundaries

This is a one-task sandbox. Restoring a task definition may replace a task and lose the synthetic ephemeral database. The adapter does not change IAM, delete data, open security groups, rotate secrets, repair arbitrary infrastructure or guarantee an AI fix for every cloud problem. It restores an explicitly approved deployment and verifies the checks actually configured. Private endpoints, production TLS, durable databases and safe rollout of fresh repaired images are separate integration work.

Cleanup when finished:

```powershell
./aws/cleanup.ps1 -Profile YOUR_PROFILE -Region us-east-1
```

The script deletes only the named sandbox and its registry stacks and waits for deletion. Registered fault task-definition revisions may remain in ECS; inspect/deregister those lab revisions if desired. Secrets can remain pending deletion during their recovery window. Confirm deletion in your account so the task is no longer billed.

Source: [ECS deployment circuit breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html).
