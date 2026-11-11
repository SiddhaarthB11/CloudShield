# Phase 7 — Isolate a repair candidate

## What changed

In earlier phases, the Judge measured the running app. Here CloudShield prepares a **candidate**, a separate copy of the app with a proposed change. The lab patch deliberately changes the health endpoint's response from `true` to `false`. That gives you a visible way to prove which copy you are talking to. Phase 8 will make the separate decision about whether a patch is an acceptable repair.

Start with `examples/candidate-health.patch`. A Git patch shows the lines to remove and add. Next read `prepare_candidate()` and `run_candidate()` in `cloudshield/candidate.py`.

| Piece | What it does |
|---|---|
| Git worktree | Gives CloudShield a separate checkout of the same Git repository. The patch is applied and committed there. Your original checkout stays on its current commit. |
| Explicit patch scope | Allows only the Python files named on the command line. CloudShield checks the patch before applying it and confirms the staged changes afterward. The Robot suite stays in the CloudShield project. |
| Unique Compose project | Uses a separate container network, database volume, and build from the candidate worktree. The API defaults to port `18002`, so the Phase 1 and 2 ports stay available. |
| Fresh lab settings | Generate a new password and signing key and seed the same synthetic admin account in the candidate's database. |
| Candidate manifest | Points the existing Judge at the candidate's source, Docker project, credentials, and port. It reuses the same trusted Robot suite. |

## 1. Prepare the patch

From the `cloudshield` folder, run:

```powershell
$candidate = python -m cloudshield candidate prepare examples/target.json examples/candidate-health.patch --allow-path backend/app/api/routes/utils.py | ConvertFrom-Json
$candidate | Select-Object project, baseline_revision, candidate_revision, manifest
```

The pinned FastAPI checkout needs to be clean. Python 3.12 is enough to prepare a patch. The output shows a new candidate commit, a unique project ID, and a path to the candidate manifest. This step doesn't contact Docker.

```powershell
git -C ../fastapi-target status --short
git -C $candidate.worktree show --stat HEAD
```

Expect no changes in the original checkout and one changed file in the candidate commit. If you change `--allow-path` to another file, CloudShield should reject the patch before creating a worktree.

## 2. Start the candidate next to the original

Start Docker Desktop and keep your Phase 1 app running on port `18000`.

```powershell
python -m cloudshield candidate start $candidate.project
Invoke-RestMethod 'http://127.0.0.1:18000/api/v1/utils/health-check/'
Invoke-RestMethod "http://127.0.0.1:$($candidate.port)/api/v1/utils/health-check/"
```

Expect `True` from the original and `False` from the candidate. Compose's shallow health check looks for a successful HTTP response. The candidate can return HTTP 200 with a `false` body and still start. The Robot check also validates the body and should catch the change.

The start command records the candidate image ID, container ID, and source revision in `deployment.json` under its run folder. With the Python 3.14 virtual environment and Robot from Phase 4, try the same Judge on that target:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield judge $candidate.manifest --report runs/phase7-judge.json
```

Expect `robot/api-health` to fail. In the report, check that the `deployment.source_revision` equals `candidate_revision` from the prepare command. The start command records what it deployed. The Judge shows how that build behaved.

## 3. Clean up

```powershell
python -m cloudshield candidate discard $candidate.project
```

`discard` removes that candidate's containers, network, and database volume. It keeps the Git worktree and run reports for inspection. Use `candidate stop` instead if you want to keep that candidate's data for a later restart.

## Explain it back

1. Why does a worktree protect the source checkout you are already editing?
2. Why do you need both a separate port and a separate database volume?
3. How can the candidate pass Docker's startup health check and fail Robot's health test?
4. Which IDs let you tell which exact candidate you ran?
