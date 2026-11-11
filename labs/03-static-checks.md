# Phase 3 — Check syntax and configuration before startup

## What changed

Read `check_python()` and `validate_config()` in `cloudshield/checks.py`. Python parses and compiles the source without importing it or executing its statements. The config checker validates required keys, types, and the port range. These checks can run even while Docker is stopped.

## Try it

The target image runs Python 3.14. Use that same version for a source scan so the checker understands the same syntax. With Python 3.14 available through the Windows launcher and the Phase 1 checkout beside this `cloudshield` folder, run:

```powershell
py -3.14 -m cloudshield static --python-version 3.14 --source ../fastapi-target/backend/app --config examples/reference.json
```

For a fault you control, create `runs/syntax-lab/broken.py` with `def broken(` on its own line. This tiny fixture also works with your existing Python. Then run:

```powershell
python -m cloudshield static --source runs/syntax-lab --config examples/reference.json
```

Expect a `syntax/broken.py` failure. Fix the file to a valid two-line function. Next, copy the reference configuration to `examples/bad-config.local.json`, change `DB_PORT` to the string `"5432"`, and rerun with that file. Valid JSON can still violate the application’s configuration rules.

## Explain it back

1. How can a syntax check run before the app starts?
2. What is the difference between syntactically valid JSON and valid application configuration?
3. Would this check catch a correctly formatted hostname that points to the wrong service?
