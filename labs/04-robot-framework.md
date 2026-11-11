# Phase 4 — Exercise the running API with Robot Framework

## What changed

Start with `suites/target.robot`. It names the three expected behaviors. Then read `cloudshield/robot_keywords.py`. Each Python keyword makes one or more HTTP requests and checks the result. The seeded-user check logs in and calls `/users/me`, which requires the API to look up the user in Postgres.

## Try it

Start the Phase 1 target first. From this `cloudshield` folder, create a local Python environment and run the suite. Use Python 3.14 so you can reuse this environment for the Judge's source checks in Phase 5.

```powershell
py -3.14 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
& ./.venv/Scripts/python.exe -m robot --outputdir runs/robot suites/target.robot
```

Expect three passing checks. Open `runs/robot/report.html`. Robot writes the raw results to `output.xml` in that folder. The Python library reads the synthetic login password from the Phase 1 credentials file and keeps it out of the Robot arguments.

Use the Phase 1 lab to stop Postgres, then rerun Robot. Predict which check will fail and which may still pass. Restart Postgres and rerun the suite to confirm recovery.

## Explain it back

1. Which part describes expected behavior and which part actually sends HTTP requests?
2. Why does the database-backed check catch a failure the health check misses?
3. What could a successful anonymous user lookup tell you about an authentication regression?
