# Phase 20 — Repair a syntax error before Docker

The watcher snapshots your saved files. A compiler failure in an allowed Python file can now trigger Gemini before the app can start. Each accepted intermediate edit is recompiled in the repair checkout. When another existing error appears, the Fixer gets the new diagnostic. After compilation passes, the Reviewer checks the combined diff, and CloudShield builds and tests the exact candidate.

## Try the supplied fault

With Docker Desktop open and Gemini configured, run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run syntax --gemini
```

Expect `matched` and `verified` when the proposed fix passes. Open `syntax_repair_report` for the compiler attempts, combined diff, review, and candidate Judge report.

## Try two errors on save

Prepare a fresh disposable checkout from the pinned template, then point a local manifest at it:

```powershell
$lab = Join-Path (Get-Location).Path 'runs/syntax-lab-target'
git -C ../fastapi-target worktree add --detach $lab 84c5a9e11a9e58262599e3d772f5386924e02ca7
$manifest = Get-Content -Raw 'examples/target.json' | ConvertFrom-Json
$manifest.source_dir = Join-Path $lab 'backend/app'
$manifest | ConvertTo-Json | Set-Content 'examples/syntax.local.json' -Encoding utf8
Join-Path $manifest.source_dir 'api/routes/utils.py'
```

Open the printed file. In `health_check()`, leave `return True` and put two deliberately incomplete assignments above it, on separate lines at the same indentation, such as `first =` and `second =`. Save, then run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield watch examples/syntax.local.json --once --runtime --gemini --allow-path backend/app/api/routes/utils.py
```

The model may fix both errors in one patch or need multiple attempts. The report distinguishes `partial` from `compiled`. Your editor still has the planted changes. The verified patch is in the repair report folder. This lab uses the local watcher to repair syntax, so the GitHub workflow's static preflight still reports syntax failures.

**Explain it back.** Why can CloudShield use the compiler before it has any running app to test?
