# Phase 5 — Turn test outcomes into a report

## What changed

Read `examples/target.json`, then `cloudshield/judge.py`. The manifest connects a Git checkout, expected config, Compose project, runtime URL, and Robot suite. The Judge runs static checks first, compares the deployed settings, converts Robot XML into JSON, and verifies that every required Robot check appears. `cloudshield/__main__.py` summarizes the results and returns an exit code.

## Try it

Use the checkout and Python environment from the prior labs. With the app running, run:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield judge examples/target.json --report runs/judge.json
$LASTEXITCODE
```

Expect `0` when everything passes, `1` for a confirmed check failure, and `2` if there was a runner error, an unavailable target, or a required check could not be verified. Open `runs/judge.json` and find `robot/database-backed-user-lookup`.

Copy `examples/target.json` to `examples/target.local.json` and change its URL to an unused local port. Run the Judge with that copy. It should report `inconclusive` because the port does not match the deployed backend. Compare that with a known deployment returning an HTTP response that violates a test assertion.

The report records the source revision, image ID, and container ID. The Judge checks that the image's revision label matches a clean Git checkout, the URL matches the port published by that container, and the same deployment is still present after testing. This is a check against the labels from our trusted local build. It does not independently prove the image contents came from Git.

## Explain it back

1. Why should a missing required test never count as a pass?
2. Why separate connectivity errors from assertions?
3. Why do stable check IDs matter when comparing an original build and a repair candidate?
