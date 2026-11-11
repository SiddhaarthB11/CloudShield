# Phase 23 — Restore configuration and deploy a verified fix

Use these actions only on a disposable managed lab. Configuration recovery compares the approved manifest with the canonical Compose inputs, then recreates the backend on the same image. Promotion uses the exact image that passed the candidate checks. The original database remains attached.

## Restore a wrong hostname

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run config-drift --heal
```

This starts a healthy isolated lab, plants the wrong hostname on the running backend, and restores the approved configuration. Expect `matched`, `recovered`, and `runtime/config/DB_HOST` in the observed failures. Inspect `configuration-action.json` and `recovery-judge.json`. A monitor can enable the same action with `--restore-config` against the managed manifest.

## Automatically promote a reviewed fix

```powershell
$python = './.venv/Scripts/python.exe'
$route = 'backend/app/api/routes/utils.py'
$lab = & $python -m cloudshield candidate prepare examples/target.json examples/candidate-health.patch --allow-path $route --port 18012 | ConvertFrom-Json
& $python -m cloudshield candidate start $lab.project
try {
    & $python -m cloudshield monitor $lab.manifest --once --gemini --allow-path $route --port 18013 --apply-verified-code
} finally {
    & $python -m cloudshield candidate discard $lab.project
}
```

A successful result is `recovered`, meaning the image passed checks **on the monitored deployment**. Inspect `promotion.json` and `promotion-judge.json`. If verification fails, CloudShield redeploys the previous image and checks that the previously passing checks still pass. A rollback restores the prior tested state, which may still contain the original bug. A failed rollback is reported separately. The automated lab tests simulate this rollback path.

Use `--restore-image` on the same managed monitor to restore an image that drifts from the recorded approved image. CloudShield checks the approved image's revision label before applying it, and then verifies the live deployment.

**Explain it back.** What extra evidence turns a verified candidate into a recovered deployment?
