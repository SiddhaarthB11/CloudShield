# Phase 13 — Catch a breaking save before you commit

Phase 12 gave a verified fix a branch name. Phase 13 adds the local-save trigger. Keep the watcher running in a terminal while you work. It checks the starting state and then checks again when the watched files change and settle.

## What happens on a save

1. **Capture.** CloudShield reads the current contents of Python files under `backend/app` and the JSON configuration selected by the manifest. It includes uncommitted Python edits, additions, and deletions. Git-ignored additions stay outside the snapshot. Files outside the watched Python scope come from the current commit in a runtime build.
2. **Debounce.** The watcher waits for the contents to stay stable for one second. Multiple saves close together produce one run. It does not repeat a run while the snapshot stays the same.
3. **Check.** CloudShield runs Python syntax checks and validates the JSON configuration. A syntax error blocks the Docker build. The JSON check validates the expected keys and types. A runtime lab uses its own generated database credentials and configuration.
4. **Test if enabled.** With `--runtime`, CloudShield puts the captured Python files into a separate Git worktree. If the snapshot has changes, it gives them a temporary Git commit so it can build and identify the exact snapshot. It runs the app and Robot checks in an isolated Docker environment on port `18004`.
5. **Repair if enabled.** With `--runtime --gemini`, a confirmed runtime failure can start one repair attempt. The Fixer and Reviewer use the same pipeline from Phases 10 and 11. The repair candidate uses port `18005`. The watch report links the repair report. Your editor's files and Git branch stay as you left them.

Read `capture()`, `watch()`, and `check_snapshot()` in `cloudshield/watcher.py`. A **snapshot** is the captured version of your saved files. A **fingerprint** identifies that snapshot so CloudShield can tell if it changed. **Debouncing** is the wait that groups rapid saves into one run.

## 1. Create a disposable checkout for the exercise

Run from the `cloudshield` folder in PowerShell. These commands create a separate checkout for edits and a local manifest for the watcher. The Git command is skipped if the lab checkout already exists.

```powershell
$labRepo = Join-Path (Get-Location).Path 'runs/watch-lab-target'
if (-not (Test-Path -LiteralPath $labRepo)) {
    git -C ../fastapi-target worktree add --detach $labRepo 84c5a9e11a9e58262599e3d772f5386924e02ca7
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the lab checkout.' }
}
$manifest = Get-Content -Raw -LiteralPath 'examples/target.json' | ConvertFrom-Json
$manifest.source_dir = Join-Path $labRepo 'backend/app'
$manifest | ConvertTo-Json | Set-Content -LiteralPath 'examples/watch.local.json' -Encoding utf8
$route = Join-Path $manifest.source_dir 'api/routes/utils.py'
$route
```

Open the printed file in VS Code. This is the disposable lab copy. Leave your main FastAPI checkout alone.

## 2. Catch a syntax error without Docker

```powershell
& ./.venv/Scripts/python.exe -m cloudshield watch examples/watch.local.json
```

Wait for the initial `pass`. In the lab copy, change the last `return True` to `return (` and save. Expect `fail`. Open the path in `static_report` and look for `syntax/api/routes/utils.py`. Undo that edit and save. Expect `pass` again. Press **Ctrl+C** to stop the watcher.

If you want a single run, add `--once`. If you want longer to finish a series of edits, add `--debounce 2`.

## 3. Catch a runtime regression and try a repair

Open Docker Desktop. Change the lab endpoint from `return True` to `return False` and save. It is valid Python, so the syntax check passes. Run this command from the same CloudShield folder:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield watch examples/watch.local.json --once --runtime
```

The Docker build can take longer than the syntax check. Expect a runtime `fail` on `robot/api-health` in the file named by `judge_report`. The watcher cleans up the snapshot containers and database volume after testing.

Leave the broken edit saved and use the same manifest to try the AI repair. This uses your existing Gemini settings and allows changes only to the lab route file.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield watch examples/watch.local.json --once --runtime --gemini --allow-path backend/app/api/routes/utils.py
```

If Gemini proposes and approves the fix and the candidate passes verification, expect `verified`. Open the `repair_report` to compare the baseline and candidate. The lab file still says `return False`. You can undo that edit when done. A Phase 13 repair is tied to the captured local snapshot. Publication against a branch requires that branch to point at the failing revision.

## Explain it back

1. How can the watcher catch an error when you have never committed the edit?
2. Why does `return (` fail before Docker while `return False` reaches Robot?
3. Why do the original checkout, snapshot environment, and repair environment stay separate?
