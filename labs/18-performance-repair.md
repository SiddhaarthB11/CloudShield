# Phase 18 extension — Repair a performance regression

Use the same JMeter workload on a failing deployment and on its proposed repair. In this lab, the profile endpoint still returns the correct user, but a new line delays every profile read by 700 ms. Robot should pass. JMeter should detect the slowdown.

## How the gate works

1. The Judge runs the normal checks. With `--performance normal` or `--performance peak`, it also runs the same JMeter workload twice on that deployment.
2. A pass requires both runs to meet the p95, error rate, and successful-throughput budgets. A repeated breach is a failure. Conflicting results are inconclusive.
3. With Gemini enabled, CloudShield can propose a Python patch when both runs miss the p95 budget, both stay within the error budget, and the normal checks passed. It sends the scoped source and the workload, budget, and measured numbers. The Reviewer assesses the exact patch. Throughput-only or high-error results are not eligible for this latency repair.
4. CloudShield builds a separate candidate, reruns Robot and both JMeter trials, and checks that the workload, Compose definition, and inspected backend command and CPU/memory settings match. The candidate must pass every check. The original deployment is left as it was.

| Gate | p95 for successful requests | Errors | Successful requests per second |
| --- | --- | --- | --- |
| `normal` | ≤ 300 ms | ≤ 1% | ≥ 4 |
| `peak` | ≤ 500 ms | ≤ 1% | ≥ 12 |

These are lab budgets. Stress and worker comparisons remain separate measurements. Run this exercise with JMeter configured as in the [Phase 18 setup](18-performance.md) and Docker Desktop open.

## 1. Start the disposable slow app

Run from the `cloudshield` folder. The lab applies the slow patch in a separate worktree, so your ordinary FastAPI checkout stays as it was.

```powershell
$python = './.venv/Scripts/python.exe'
$route = 'backend/app/api/routes/users.py'
$slow = & $python -m cloudshield candidate prepare examples/target.json examples/performance-slow.patch --allow-path $route --port 18010 | ConvertFrom-Json
& $python -m cloudshield candidate start $slow.project
```

## 2. Repair and verify

Try the deterministic repair first. It removes the planted delay, so this exercise works without a Gemini key.

```powershell
& $python -m cloudshield repair $slow.manifest --patch examples/performance-fix.patch --allow-path $route --port 18011 --max-attempts 1 --performance normal
```

To have Gemini propose and review the patch, use this command against the same slow deployment:

```powershell
& $python -m cloudshield repair $slow.manifest --gemini --allow-path $route --port 18011 --max-attempts 1 --performance normal
```

Expect the baseline to show a failure on `performance/normal` with the Robot checks passing. A successful repair is `verified` and lists `performance/normal` under `resolved_targets` in the verdict. Compare the two p95 values in `baseline.json` with the two in `attempt-1-judge.json`. The `performance.artifacts` paths point to the JMeter CSV and logs for each trial.

When done, clean up the slow deployment:

```powershell
& $python -m cloudshield candidate discard $slow.project
```

## 3. Use the same gate automatically

Before cleaning up, you can monitor the slow deployment on a timer:

```powershell
& $python -m cloudshield monitor $slow.manifest --performance normal --gemini --allow-path $route --port 18011
```

The monitor waits five minutes between completed checks by default with JMeter enabled. It deduplicates repeated incidents. Use **Ctrl+C** to stop it and then discard the slow deployment. You can also add `--performance normal` to a local `watch --runtime` command. With `--gemini`, a qualifying saved regression can start the same reviewed repair flow. Use these modes against disposable test deployments.
