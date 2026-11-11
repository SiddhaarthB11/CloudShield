# Phase 11 — Review the patch before running it

In Phase 10, the Fixer proposed a patch and the Controller built it for testing. Phase 11 adds a second Gemini call that reviews the exact change before the Controller builds the candidate image.

## Follow the workflow

| Step | Component | What it does |
| --- | --- | --- |
| 1 | Judge | Confirms the failure on the existing deployment. |
| 2 | Fixer | Proposes a small change to the files you allowed. |
| 3 | Controller | Applies the patch in a separate Git worktree, commits it locally, and reads the diff from the baseline commit to the candidate commit. |
| 4 | Reviewer | Receives that diff and the failed checks. It considers whether the change addresses the failure or hides it, weakens authorization, or changes unrelated behavior. |
| 5 | Candidate runner | After approval, builds the image and starts it in an isolated Docker environment. |
| 6 | Judge and Verifier | Rerun the checks and verify that the original failure was resolved, no passing check regressed, and the expected image was tested. |

Read `review_gemini()` in `cloudshield/reviewer.py` and the review stage of `attempt_repair()` in `cloudshield/controller.py`. The Reviewer gets its own instructions and returns a structured decision and reason. It sees the change and failed checks without the Fixer's rationale or raw logs.

The Git worktree is a separate local copy where CloudShield can prepare the proposed change. Docker comes after review because applying a patch to a worktree lets CloudShield inspect the exact code change before running it. The original deployment remains available throughout the lab. A verified candidate is evidence for a later promotion step.

## 1. Load the broken baseline

Open Docker Desktop and run these commands in PowerShell from the `cloudshield` folder. This reuses the Phase 9 deployment and your existing local Gemini settings.

```powershell
$phase9 = Get-Content -Raw -LiteralPath 'runs/repairs/35273e485dc2/baseline-evidence.json' | ConvertFrom-Json
$broken = Get-Content -Raw -LiteralPath "runs/candidates/$($phase9.project)/candidate.json" | ConvertFrom-Json
Invoke-RestMethod "http://127.0.0.1:$($broken.port)/api/v1/utils/health-check/"
```

Expect `False`. If the saved deployment is stopped, run `python -m cloudshield candidate start $broken.project` and retry the request. If the saved files are missing, recreate the broken deployment using the [Phase 9 lab](09-controller.md).

## 2. Run the Fixer and Reviewer

```powershell
& ./.venv/Scripts/python.exe -m cloudshield repair $broken.manifest --gemini --review --allow-path backend/app/api/routes/utils.py --port 18003 --max-attempts 1
```

`--gemini` selects the AI Fixer. `--review` requires a separate review before the candidate can run. By default the Reviewer uses Gemini 3.1 Flash-Lite. Use `--review-model` to select another Gemini model with structured output support. [Gemini 3.1 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite)

## 3. Read the audit trail

Open the directory named by the command's `report` field.

- `attempt-1.patch` contains the proposed patch.
- `attempt-1-reviewed.patch` contains the exact Git diff sent for review.
- `attempt-1-review.json` records the decision, reason, model, candidate commit, and SHA-256 fingerprint of that diff.
- `attempt-1-verdict.json` is created if the run reaches verification. For the health lab, look for `robot/api-health` in `resolved_targets` and an empty `regressions` list.

A review decision of `reject` ends that attempt before Docker builds. If the Reviewer cannot respond or its response is invalid, the run stops as `inconclusive` and records the problem in the attempt's `detail` field. An `approve` decision lets the isolated tests run. The Verifier can still reject a patch that introduces a regression.

## Practice offline

```powershell
python -m unittest discover -s tests -p test_reviewer.py -v
python -m unittest discover -s tests -p test_controller.py -v
```

The tests simulate the Reviewer and demonstrate that a rejected patch never starts Docker and that an approved patch can still fail the regression guard.

## Explain it back

1. What does the Reviewer see, and why is its input separate from the Fixer's explanation?
2. Why does CloudShield create a local Git commit before building an image?
3. If the Reviewer approves a patch that fixes health but breaks admin authorization, what stops that patch from being verified?
