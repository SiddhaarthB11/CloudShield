# Phase 19 — Diagnose and group failures

CloudShield records a diagnosis alongside its existing check results. A failed login can be a runtime code failure, a configuration incident, or a symptom of an unavailable service. When service state or configuration evidence explains the failure, CloudShield gives that evidence priority. The diagnosis lists the related check IDs together.

From the CloudShield folder, run these independently with Docker Desktop open:

```powershell
$python = './.venv/Scripts/python.exe'
& $python -m cloudshield fault run login
& $python -m cloudshield fault run config-drift
& $python -m cloudshield fault run dependency
```

Inspect `diagnosis` in each output. Expect `runtime` for the planted login code bug, `configuration` for the mismatched database secret reference, and `deployment` with cause `db_unavailable` for the stopped database. The latter two are inspection labs here. Phases 22 and 23 add approved recovery.

Read `diagnose()` in `cloudshield/incidents.py`. This follows the internship pattern of grouping symptoms before choosing a Fixer. A classification is evidence for routing. It does not give a model permission to run an action.

**Explain it back.** Why could sending every failed check to the same code Fixer make a database outage worse?
