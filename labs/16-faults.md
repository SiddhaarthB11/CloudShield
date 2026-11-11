# Phase 16 — Run repeatable faults

The fault catalog gives you one experiment at a time. Each case defines the fault, the check that should catch it, and the expected outcome. CloudShield starts from the pinned FastAPI commit in a separate Git worktree. Runtime cases also get a separate Docker project with a fresh database.

| Scenario | What CloudShield changes | Expected result |
| --- | --- | --- |
| `syntax` | Leaves an unclosed parenthesis in the health endpoint | `detected` by Python before Docker starts |
| `app-logic` | Makes the endpoint return `false` | `verified` after the patch restores health and the other checks pass |
| `regression` | Proposes a patch that restores health but changes the anonymous response to HTTP 418 | `rejected` by the Verifier because the anonymous-access check regressed |
| `config-drift` | Changes the running backend's database secret reference label | `handoff` because the observed setting differs from the expected configuration |
| `startup` | Makes the backend process exit at startup | `handoff` with the known exit code and container log marker |
| `network` | Disconnects the still-running database from the lab network | `handoff` because the API's database-backed check fails |
| `dependency` | Stops the lab database | `handoff` because the database is stopped and the database-backed check fails |

## 1. Try a fault without Docker

From the `cloudshield` folder, list the cases and run the syntax case:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault list
& ./.venv/Scripts/python.exe -m cloudshield fault run syntax
```

Expect `status: matched`, `outcome: detected`, and `syntax/api/routes/utils.py` in `observed`. Here, **matched** means the experiment produced the expected result. For this experiment the result is a detected error. Open `static_report` to see the failing line.

## 2. Show three different outcomes

With Docker Desktop open, run these one at a time:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run app-logic
& ./.venv/Scripts/python.exe -m cloudshield fault run regression
& ./.venv/Scripts/python.exe -m cloudshield fault run dependency
```

Each should return `matched`, with outcomes `verified`, `rejected`, and `handoff` respectively. The first two use known patches. That makes the regression exercise repeatable and lets you demo both outcomes even if Gemini is unavailable. To have Gemini generate and review the app-logic fix, run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run app-logic --gemini
```

Open the path in `report`. It links the available static checks, Judge report, repair report, or Docker evidence. If a report says `unexpected`, compare `observed` with `expected`. If it says `inconclusive`, look for `detail` or `cleanup_detail`, and follow the linked repair report when present.

## 3. Compare the infrastructure faults

```powershell
& ./.venv/Scripts/python.exe -m cloudshield fault run config-drift
& ./.venv/Scripts/python.exe -m cloudshield fault run startup
& ./.venv/Scripts/python.exe -m cloudshield fault run network
```

A database that is **running but disconnected** can break the same login check as a database that is **stopped**. The Docker evidence distinguishes those cases. The config case demonstrates a changed metadata reference and does not change the database password.

The fault and repair use ports `18007` and `18008`. Use `--port` and `--repair-port` if those are occupied. CloudShield cleans up the lab containers and database volume, leaving the reports and worktree for inspection. Your Phase 1 app and your editor checkout are separate.

## Explain it back

1. Why is `matched` the right result even when a deliberately bad repair gets rejected?
2. Which check tells you a repair restored health, and which check tells you it broke another behavior?
3. Why can both a stopped database and a disconnected database produce a failed login?
