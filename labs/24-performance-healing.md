# Phase 24 — Run performance repair end to end

This command packages the Phase 18 performance-repair exercise into a disposable fault run. The planted Python change adds 700 ms to an authenticated profile read while preserving the response. Robot should pass and JMeter should catch a repeatable latency breach.

With Docker Desktop open and JMeter configured using the [Phase 18 setup](18-performance.md), run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run latency
& ./.venv/Scripts/python.exe -m cloudshield fault run latency --gemini
```

The first command uses the known patch. The second asks Gemini to propose it and the Reviewer to review it. Both require the same JMeter profile and the functional checks on the candidate. If JMeter lives in a different folder, supply `--jmeter-home`.

Expect `performance/normal` under the resolved checks and `verified` only when both candidate runs meet the budget. Use the repair report to compare baseline and candidate p95, errors, and throughput. Look at `performance.artifacts` for the raw JMeter samples. A mixed result is inconclusive and should not launch an AI repair.

**Explain it back.** Why does one slow JMeter run provide less evidence than a repeatable slowdown under the same workload?
