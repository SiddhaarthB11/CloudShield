# Phase 10 — Ask the AI Fixer for a patch

## What changed

In Phase 9 you supplied `repair-health.patch`. With `--gemini`, CloudShield asks Gemini what to change after the Judge confirms a supported runtime failure. The model proposes source edits. CloudShield validates those edits, creates a Git patch, and uses the existing worktree, Docker candidate, Robot tests, and Verifier to decide whether it worked.

Start with `propose_gemini()` and `make_patch()` in `cloudshield/fixer.py`. `propose_gemini()` selects the failed check, its expected behavior, the HTTP status when available, basic container status, and the files you explicitly allow. It reads those files from the tested Git revision. The API receives the selected source and those diagnostics. The credentials file, API key value, raw Docker logs, and hand-written repair patch stay out of the prompt. The API key goes in the request's authentication header.

Gemini returns a summary and a structured list of edits. Each edit names a file, an exact block of existing lines, and the replacement lines. CloudShield rejects edits outside the approved paths, ambiguous matches, and invalid Python. The Judge and Verifier still decide whether the resulting candidate fixed the failure. The independent AI Reviewer comes in Phase 11.

## 1. Open the right folder

Open Docker Desktop and run the following commands in PowerShell from the `cloudshield` folder, where `requirements.txt` lives. If your prompt ends in `outputs`, run `cd .\cloudshield` first. Use the Python 3.14 `.venv` you set up in Phase 4.

Your key is already saved in `.env.gemini.local` beside `requirements.txt`. It works across new PowerShell sessions. CloudShield reads it when it contacts Gemini, and Git ignores it. A `GEMINI_API_KEY` already set in PowerShell takes precedence. Only allow files you are authorized to send to Google. This lab sends selected public FastAPI source and synthetic test diagnostics.

```powershell
& ./.venv/Scripts/python.exe --version
Test-Path -LiteralPath '.env.gemini.local'
docker ps
```

Expect Python `3.14.x`, `True` for the settings file, and Docker's container list. `Test-Path` checks for the file without displaying the key.

## 2. Load the broken Phase 9 baseline

These commands work in a fresh PowerShell window. They find the broken deployment used in your successful Phase 9 run and load its saved manifest.

```powershell
$phase9 = Get-Content -Raw -LiteralPath 'runs/repairs/35273e485dc2/baseline-evidence.json' | ConvertFrom-Json
$broken = Get-Content -Raw -LiteralPath "runs/candidates/$($phase9.project)/candidate.json" | ConvertFrom-Json
Invoke-RestMethod "http://127.0.0.1:$($broken.port)/api/v1/utils/health-check/"
```

Expect `False`. Phase 9 verified a fix on a separate deployment, so this baseline still has the original bug. If this saved deployment is stopped, restart it with `python -m cloudshield candidate start $broken.project` and repeat the health request. If the saved files are missing, recreate `$broken` with the first two commands in the [Phase 9 lab](09-controller.md). If the endpoint returns `True`, check which deployment is answering before going further.

## 3. Ask Gemini to propose the repair

```powershell
& ./.venv/Scripts/python.exe -m cloudshield repair $broken.manifest --gemini --allow-path backend/app/api/routes/utils.py --port 18003 --max-attempts 1
$LASTEXITCODE
```

This runs the Judge against the broken app on port `18002`. If the Judge confirms the failure, CloudShield makes one proposal attempt. A usable patch is tested on a new candidate on port `18003`. The default model is `gemini-3.5-flash`. If your API project uses another Gemini model with structured output support, choose it with `--model`. [Google GenerateContent API](https://ai.google.dev/api/generate-content?hl=en)

## 4. Read the result

Look for `patch_source: gemini`, `fixer_proposals`, and the `report` path. Open `attempt-1.patch` in that run folder if a patch was generated. If the verdict is `verified`, inspect `attempt-1-verdict.json`. For this lab, look for `robot/api-health` under `resolved_targets` and an empty `regressions` list. If the run is `inconclusive`, read `stage` and `detail`. A Gemini error at `fixer` differs from a Docker build failure at `start` in the attempt record.

The broken deployment stays available for inspection. When you are done with that lab environment, run `python -m cloudshield candidate discard $broken.project`.

## Practice without an API key

```powershell
python -m unittest discover -s tests -p test_fixer.py -v
```

These tests use a simulated Gemini response. They show how CloudShield converts a valid proposal into a patch and blocks an out-of-scope or invalid edit.

## Explain it back

1. What did you supply in Phase 9, and what does Gemini supply in Phase 10?
2. Why do you give the model both the failed test's expectation and the relevant app source?
3. What is the difference between a valid Git patch and a verified repair?
