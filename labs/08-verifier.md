# Phase 8 — Verify a candidate by check ID

## What changed

Phase 7 gives a patch its own place to run. Phase 8 decides whether the measured results justify calling that patch a verified repair. Start with `verify()` in `cloudshield/verifier.py`.

The Verifier reads three files. The **baseline Judge report** shows the results before the patch. The **candidate Judge report** shows the results on the patched build. The candidate's **`deployment.json`** records the image and container CloudShield actually launched. The Judge now also records a test profile containing hashes of the Robot suite and Python keywords, the Python version, and the required Robot check names. A hash is a short fingerprint of the file contents. If a test file changes, its fingerprint changes.

| Decision | Meaning |
|---|---|
| `verified` | The baseline had a confirmed failure, the exact candidate ran the same checks, and all candidate checks passed. |
| `rejected` | The evidence was usable and the candidate still failed a check. A previously passing check that fails is listed as a regression. |
| `inconclusive` | CloudShield can't establish a valid comparison. Examples include a missing required check, changed test definitions, a different running container, or two green runs with no failure to repair. |

The PowerShell exit codes are `0`, `1`, and `2` in that order. You can select target failures explicitly with repeated `--target-check` flags. By default the Verifier uses the confirmed baseline failures. Either way, the candidate must pass the full suite.

## 1. Reproduce the equal-count regression offline

From the `cloudshield` folder, run:

```powershell
python -m unittest discover -s tests -p test_verifier.py -v
```

Open `test_equal_failure_count_does_not_hide_a_regression` in `tests/test_verifier.py`. The baseline has one failing health check. The candidate passes health and fails anonymous access. Each report has one failure. The test proves the Verifier records health under `resolved_targets`, anonymous access under `regressions`, and returns `rejected`. The test itself passes because the rejection is what we expected.

## 2. Verify the Phase 7 candidate with live reports

Keep the baseline running and use the `$candidate` from the Phase 7 walkthrough. Start that candidate first so its run folder contains `deployment.json`. Rerun both Judge commands after updating to Phase 8 because older JSON reports lack the test profile.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield judge examples/target.json --report runs/phase8-baseline.json
& ./.venv/Scripts/python.exe -m cloudshield judge $candidate.manifest --report runs/phase8-candidate.json
$record = Join-Path (Split-Path $candidate.manifest) 'deployment.json'
python -m cloudshield verify runs/phase8-baseline.json runs/phase8-candidate.json --candidate-record $record --report runs/phase8-verdict.json
$LASTEXITCODE
```

The Phase 7 patch deliberately breaks the response body of the health endpoint. Expect `rejected`, exit code `1`, and `robot/api-health` under `regressions`. The baseline for this live exercise is healthy. The offline example above models the separate case where a real failure was fixed at the cost of introducing a new one.

## Explain it back

1. Why can the same failure count represent a worse candidate?
2. Why does the Verifier need the candidate's start record in addition to a green report?
3. What happens if someone removes the failing Robot test from the suite and runs it again?
4. Why does a verified verdict establish test evidence while leaving merge and promotion for later phases?
