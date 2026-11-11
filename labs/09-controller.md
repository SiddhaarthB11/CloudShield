# Phase 9 — Run a complete repair with a known patch

## What changed

The Controller ties together the tools you ran individually in Phases 5–8. It runs the Judge on the baseline. A confirmed failure on an identified deployment triggers evidence collection and a repair attempt. Each attempt prepares a separate worktree, starts a fresh Compose candidate, runs the Judge, and asks the Verifier whether the repair passed. It then discards that candidate's Docker resources and saves the patch, worktree, and reports for inspection.

Read `repair()` and `attempt_repair()` in `cloudshield/controller.py`. The first chooses when to start or stop. The second runs one candidate. Phase 9 takes patches from files so you can learn the controller's behavior before adding an AI Fixer.

## 1. Try the real repair

From `outputs/cloudshield`, prepare and start the deliberately broken Phase 7 candidate. If yours is still running, reuse it as `$broken`.

```powershell
$broken = python -m cloudshield candidate prepare examples/target.json examples/candidate-health.patch --allow-path backend/app/api/routes/utils.py | ConvertFrom-Json
python -m cloudshield candidate start $broken.project
```

Run the controller with the Python 3.14 environment from Phase 4. The broken deployment uses port `18002` and the repair candidate uses `18003`, so both can run together.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield repair $broken.manifest --patch examples/repair-health.patch --allow-path backend/app/api/routes/utils.py --port 18003
$LASTEXITCODE
```

Expect `verified` and exit code `0`. The known patch changes the health response back to `true`. Find `report` in the command output, then open that run folder to see `baseline.json`, `baseline-evidence.json`, and the attempt's patch, Judge, and verdict files. The broken deployment is still available on `18002` for comparison. Once done, use `python -m cloudshield candidate discard $broken.project` to remove your deliberately broken lab deployment.

If you point the command at the healthy `examples/target.json`, it should return `no_repair_needed` without creating a candidate. An inconclusive baseline also blocks repair. For example, the controller can't start from a syntax-only report with no verified runtime identity.

## 2. See how retries and cleanup work

Pass multiple `--patch` arguments to try alternatives in order. Each is applied independently against the original failing revision. Duplicate patch contents are skipped within the run. The default limit is two attempts and can be changed with `--max-attempts` up to five. A verified result stops the loop. Inconclusive verification or failed cleanup also stops it. A rejected patch or build failure can move to the next distinct patch within the budget.

```powershell
python -m unittest discover -s tests -p test_controller.py -v
```

The offline tests simulate a rejected candidate, a duplicate, and a successful second candidate. They also simulate a failed build and check that the controller still asks Docker to discard the isolated project. If Docker cannot clean up, `repair.json` records the project ID and marks the run inconclusive so you can retry `candidate discard` after recovering Docker access.

## Explain it back

1. Why does every attempt start from the original failing revision?
2. What would the report tell you if the candidate could not build?
3. Where can you see why a patch was rejected after its containers have been removed?
