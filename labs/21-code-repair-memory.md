# Phase 21 — Broaden code repair and remember failed attempts

The Fixer selects relevant files within your allowed scope. CloudShield records rejected diffs and review reasons by repository, source revision, and failed check IDs. If an AI candidate fixes some checks without introducing a regression, CloudShield can keep that progress while asking for the next edit. Every cumulative patch is still tested against the original failure and all original checks.

## Repair two different bugs

```powershell
$python = './.venv/Scripts/python.exe'
& $python -m cloudshield fault run authorization --gemini
& $python -m cloudshield fault run login --gemini
```

The first fixture changes the anonymous denial to HTTP 418. The second passes the wrong value as the login username. In each repair report, compare the failing baseline check with the candidate verdict. Omit `--gemini` to use the known repair instead.

## Inspect retries and memory

Start the broken health candidate, then allow up to three proposals. A Reviewer rejection is saved and passed to the next attempt. If the first proposal passes, that run has no rejection to replay.

```powershell
$broken = & $python -m cloudshield candidate prepare examples/target.json examples/candidate-health.patch --allow-path backend/app/api/routes/utils.py --port 18012 | ConvertFrom-Json
& $python -m cloudshield candidate start $broken.project
try {
    & $python -m cloudshield repair $broken.manifest --gemini --review --allow-path backend/app/api/routes/utils.py --port 18013 --max-attempts 3
} finally {
    & $python -m cloudshield candidate discard $broken.project
}
```

Inspect `fixer_proposals` and `attempts`. The automated tests verify that rejected review feedback is retained. This keeps the repair history separate from the target app.

**Explain it back.** What should the next Fixer attempt learn from a patch that made login pass and broke authorization?
