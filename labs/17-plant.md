# Phase 17 — Plant 5 Breaks

Phase 25 supersedes the original draw and outcomes described below. Use the [current mixed-recovery lab](25-mixed-plant.md) to run the updated catalog.

One command draws five distinct scenarios from the Phase 16 fault catalog and runs them one at a time. The draw always includes the repairable `app-logic` case. A **seed** picks the other four and their order. The report records the seed and links to each individual run.

Each fault uses its own disposable environment. An invalid Python file can block runtime tests for that scenario, and a backend that exits can prevent Robot from reaching the API. Those cases are marked as blocking Robot. The other cases still get their own checks and results. When a case uses Gemini, the model gets the same scoped source and failed-check evidence as in Phase 10. The catalog's expected result stays in the lab report.

## 1. Run a predictable demo

Open Docker Desktop, then run this from the `cloudshield` folder:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield plant --seed 3
```

Seed `3` runs these cases in order:

| Order | Scenario | Expected outcome |
| --- | --- | --- |
| 1 | `dependency` | `handoff` with evidence that the database stopped |
| 2 | `app-logic` | `verified` after a known patch repairs health |
| 3 | `startup` | `handoff` with evidence of the backend exit |
| 4 | `syntax` | `detected` before Docker starts |
| 5 | `regression` | `rejected` because the candidate breaks anonymous access |

CloudShield prints progress while the tests run. At the end, expect `status: matched` and counts of two handoffs, one verification, one detected syntax error, and one rejection. **Matched** means the experiment behaved as designed. A rejection or handoff can be the correct outcome.

## 2. Draw a random set and use Gemini

```powershell
& ./.venv/Scripts/python.exe -m cloudshield plant --gemini
```

Without `--seed`, CloudShield generates and saves one. With `--gemini`, the AI Fixer and Reviewer handle the application fault. If the draw includes the regression case, CloudShield still uses the deliberately bad known patch so you can see the Verifier reject it.

## 3. Inspect or replay a run

Open the final `report` path for the batch summary. Each row has its own report with the failed check, patch and verdict, or diagnostic evidence. `blocked_checks` means those checks could not run for that case.

To replay a draw, run the first command with the seed from that report. Add `--gemini` again if the original run used it. The seed reproduces the fault selection and order. A new Gemini response or Docker run can produce a different result.

If a case reports `inconclusive`, open its individual report. CloudShield stops the batch if Docker cleanup fails so later cases do not try to reuse occupied ports. Run one batch at a time on the default ports, `18007` and `18008`.

## Explain it back

1. What exactly does the seed reproduce?
2. Why give each fault a separate environment?
3. When is `rejected` a successful lab outcome?
