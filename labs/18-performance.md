# Phase 18 — Measure performance with JMeter

Robot checks whether a request behaves correctly. JMeter makes many requests at once and measures how long they take, how many complete, and how often they fail. This lab runs against a disposable FastAPI and Postgres deployment. It measures performance separately from the fast repair checks.

## What the workload does

CloudShield starts the pinned FastAPI app in its own Docker project and logs in once as the synthetic lab admin. JMeter then sends repeated authenticated `GET /api/v1/users/me` requests. Each user waits 100 ms between requests. The request exercises FastAPI, authentication, and a database lookup. The login itself is outside the measurement window.

| Profile | Simulated users | Ramp-up | Measured window | Lab budget |
| --- | --- | --- | --- | --- |
| `normal` | 2 | 2 seconds | 15 seconds | p95 ≤ 300 ms and errors ≤ 1% |
| `peak` | 8 | 4 seconds | 20 seconds | p95 ≤ 500 ms and errors ≤ 1% |
| `stress` | 32 | 5 seconds | 25 seconds | Observation only |
| `compare` | 32 | 5 seconds | 25 seconds per trial | Same stress workload, first with one FastAPI worker and then with four |

A **simulated user** is a JMeter thread. **Ramp-up** spreads the thread starts over time. More threads create more concurrent work. They do not guarantee a specific request rate. The budget values are lab thresholds for this experiment.

## 1. Set up Java and JMeter

To download and verify JMeter and run all four profiles in order, use this shortcut from the `cloudshield` folder with Docker Desktop open:

```powershell
& ./performance/run-lab.ps1
```

To go through setup and the profiles one at a time, follow the steps below.

From the `cloudshield` folder, check Java:

```powershell
java -version
```

Download the **Apache JMeter 5.6.3 binary ZIP** and verify it using the checksum or signature on Apache's download page. The commands below assume the ZIP is in Downloads. Extract it so that `JMETER_HOME/bin/ApacheJMeter.jar` exists. [Apache JMeter downloads](https://jmeter.apache.org/download_jmeter.cgi)

```powershell
$jmeterFolder = Join-Path (Resolve-Path '../../work').Path 'jmeter'
Expand-Archive -LiteralPath "$env:USERPROFILE/Downloads/apache-jmeter-5.6.3.zip" -DestinationPath $jmeterFolder
$env:JMETER_HOME = Join-Path $jmeterFolder 'apache-jmeter-5.6.3'
Test-Path (Join-Path $env:JMETER_HOME 'bin/ApacheJMeter.jar')
```

Expect `True`. In a new terminal, set `JMETER_HOME` again or pass `--jmeter-home` with the extracted folder. Open Docker Desktop before running the workload.

## 2. Run the profiles

Run these one at a time from CloudShield. Start with `normal` so you have a baseline before adding load.

```powershell
& ./.venv/Scripts/python.exe -m cloudshield load normal
& ./.venv/Scripts/python.exe -m cloudshield load peak
& ./.venv/Scripts/python.exe -m cloudshield load stress
```

The command builds and starts the lab app, runs JMeter in CLI mode, then removes the lab containers and database volume. The results stay under `runs/load`. Open the output's `report` path. Each trial links its raw JMeter CSV in `samples.jtl`, its run log, and the Docker build identity. [Apache JMeter CLI guide](https://jmeter.apache.org/usermanual/get-started.html)

- **`p95_ms`** is the latency at or below which about 95% of successful requests finished. Reported errors are separate.
- **`requests_per_second`** counts all measured request attempts. **`successful_requests_per_second`** counts the ones that passed.
- **`error_pct`** is the share that failed, including wrong HTTP responses, assertion failures, and timeouts.

CloudShield excludes the ramp-up from its summary. It checks HTTP `200` and the synthetic user's email on each sample. If there are no usable steady-load samples, the run is `inconclusive`.

## 3. Compare one worker with four

```powershell
& ./.venv/Scripts/python.exe -m cloudshield load compare
```

A **worker** is a FastAPI server process. This runs the same stress profile twice on the same machine, sequentially, with fresh lab data. In the summary, `four_minus_one` gives the difference in successful throughput, error rate, and p95. `measured` means both trials produced usable data. Four workers may help if request processing is the bottleneck. A shared database, Docker resource limits, or the load generator itself can still limit throughput.

## Explain it back

1. Why do we measure an authenticated profile read instead of the shallow health endpoint?
2. If p95 rises but successful throughput barely changes, what would you measure next?
3. What stayed the same between the one-worker and four-worker trials?
