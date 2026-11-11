# Phase 2 — Compare environments

## What changed

Read `examples/reference.json` and `examples/second-environment.json` first. These are deliberately small configuration snapshots containing safe, synthetic values and a **reference** to a database password. The Compose file accepts a separate host port and database hostname for a second lab environment.

Read `compare()` in `cloudshield/checks.py`. It compares top-level keys, types, and values. An allowlist excuses a value difference after both values have passed the presence and type checks. It reports key names and reasons without printing values.

## Try it

```powershell
python -m cloudshield compare examples/reference.json examples/second-environment.json
python -m cloudshield compare examples/reference.json examples/second-environment.json --allow-difference DB_HOST --allow-difference DB_SECRET_REF
```

Expect the first command to fail on two differences. Expect the second to pass. Copy the second snapshot to `examples/second.local.json`, delete `DB_NAME` from the copy, and compare again. Passing `--allow-difference DB_NAME` still produces a missing-key failure.

### Run a second Compose environment

After Phase 1 works, use a **fresh PowerShell window** and change to the folder that contains `phase-1.compose.yml`. Keep the original Phase 1 setup running. The second project below uses its own network and volume and publishes its API on port `18001`. For this local lab, reuse the synthetic credentials from the walkthrough. The secret-reference labels in the example JSON are illustrative.

```powershell
$env:APP_HOST_PORT = '18001'
$env:APP_DB_HOST = 'staging-db'
$env:APP_DB_SECRET_REF = 'staging/postgres'
$env:APP_SOURCE_REVISION = git -C fastapi-target rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $env:APP_SOURCE_REVISION -ne '84c5a9e11a9e58262599e3d772f5386924e02ca7') {
    throw 'Use the pinned Phase 1 checkout before building.'
}
if (git -C fastapi-target status --porcelain --untracked-files=all) {
    throw 'Use a clean checkout to label the image with its source revision.'
}
$second = @('--env-file', '.env.phase1.local', '-f', 'phase-1.compose.yml', '-p', 'cloudshield-phase2')
docker compose @second build backend
docker compose @second up -d --wait db
docker compose @second run --rm --no-deps backend bash scripts/prestart.sh
docker compose @second up -d --wait backend
Invoke-RestMethod 'http://127.0.0.1:18001/api/v1/utils/health-check/'
docker compose @second ps
```

Run each step only after the prior step succeeds. While both projects are up, return to the `cloudshield` folder and compare settings from the **running containers**:

```powershell
python -m cloudshield snapshot --compose ci/compose.yml --env-file .env.phase1.local --project cloudshield-demo --report runs/live-reference.json
python -m cloudshield snapshot --compose ci/compose.yml --env-file .env.phase1.local --project cloudshield-phase2 --report runs/live-second.json
python -m cloudshield compare runs/live-reference.json runs/live-second.json --allow-difference DB_HOST --allow-difference DB_SECRET_REF
```

The snapshot exports only the database host, port, name, and lab secret reference. The username, password, and full connection URL stay out of the report. Leave the second project running for the combined verification lab or stop it from the second terminal with `docker compose @second stop`.

## Explain it back

1. Why can `db` and `staging-db` both be valid?
2. Why should a missing key still fail if a value difference is allowed?
3. What additional work would you need to compare the settings that are actually running in two cloud environments?
