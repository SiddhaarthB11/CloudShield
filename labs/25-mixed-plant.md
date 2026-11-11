# Phase 25 — Plant 5 Breaks with mixed recovery

The batch now draws all five scenarios randomly from the enabled catalog. It no longer forces the health endpoint bug. Each scenario has its own disposable environment. The Fixer receives the measured failure and scoped source. The expected answer stays in the lab harness.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield plant --seed 3
& ./.venv/Scripts/python.exe -m cloudshield plant --seed 3 --gemini
```

In the current catalog, seed 3 draws dependency, configuration drift, startup, login, and syntax. Expect two running deployments recovered, one startup handoff, a verified login patch, and a detected syntax fault. With Gemini enabled, the syntax fault can also produce a verified patch. Gemini handles the selected code faults. Known deployment actions execute through policy.

Omit the seed for a fresh draw. Add `--jmeter-home $env:JMETER_HOME` to include the latency scenario in the pool. To replay a run, use the same seed, catalog version, and performance setting. Seeds from the old Phase 17 catalog can produce different selections after the catalog change.

Read the batch `plant.json`, then open the linked fault reports. `verified` describes a tested patch. `recovered` describes a restored running deployment. `handoff` may be the correct response for an unsupported failure.

To exercise every available fault and prove two database recoveries with one continuously running monitor, run the acceptance lab:

```powershell
& ./labs/run-fault-acceptance.ps1
```

This uses Gemini for source and latency repairs, restores the stopped database and approved configuration, and expects a rejection or handoff where appropriate. Configure JMeter first to include latency. The summary under `runs/acceptance` links to the individual reports.

**Explain it back.** Why can a batch match its expected outcomes even when one case requires a handoff?
