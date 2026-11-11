# Run CloudShield as a standalone project

CloudShield is the runner. Your application is a separate checkout/deployment connected through a target manifest. This repository does not contain the internship Vendor SaaS platform, generated repair runs, credentials, or the FastAPI template codebase.

## Dashboard and framework tests

Install Python 3.14 and Git. Clone this repository, open PowerShell in its root and run:

```powershell
py -3.14 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
./start-dashboard.ps1
```

Open `http://127.0.0.1:18700`. The dashboard works without AWS credentials. Actual Docker observations and repairs require a running, accessible Docker Desktop installation and a configured test subject. The worker persists across browser/server restarts; use Pause all automation in the UI to stop scheduling safely.

Run the framework's own test suite:

```powershell
& ./.venv/Scripts/python.exe -m unittest discover -s tests -q
```

Tests include simulated deployment responses and real loopback HTTP requests. Passing them is different from a live Docker/AWS acceptance run. See [validation limits](VALIDATION.md).

## Optional separate demo application

Start Docker Desktop, then:

```powershell
./setup-demo.ps1 -Start
& ./.venv/Scripts/python.exe -m cloudshield judge examples/target.json
```

The setup script clones the official FastAPI template beside CloudShield as `../fastapi-target`, verifies the pinned revision and clean checkout, creates ignored credentials and starts the demo. It does not overwrite an existing target's edits. Its Compose project is `cloudshield-demo`, backend port is `18020`, and database volume is separate from the original development lab.

Select `examples/target.json` in the dashboard's Connect application form. Start with Run Judge. For automatic deployment of reviewed repairs, use the generated managed demo target flow in [phase 28](labs/28-dashboard.md). The original demo connection does not silently gain managed deployment permission.

Keep `.env.phase1.local` while reusing the same database volume. Do not replace its password while the existing database persists. Stop this demo without deleting its data:

```powershell
docker compose --env-file .env.phase1.local -f ci/compose.yml -p cloudshield-demo down
```

The older lessons refer to the development checkpoints. The portable connection in this repository uses `ci/compose.yml`, `.env.phase1.local`, project `cloudshield-demo` and port `18020`; use these values when adapting historical examples. A second live environment for phase 2 must be configured separately; JSON-only comparison works immediately.

## Optional Gemini and performance tests

Copy `.env.example` to `.env.gemini.local` and fill its blank GEMINI_API_KEY locally. Never paste the key into a commit. Enable AI only for the target's explicitly approved source files. The Fixer and independent Reviewer use that local configuration.

For JMeter workloads, install Java and Apache JMeter, then set JMETER_HOME to its extracted installation directory. Performance checks are opt-in; they are not part of every default Judge run.

## Optional AWS deployment

Follow [phase 26](labs/26-aws-connection.md) and [phase 27](labs/27-aws-recovery.md). Configure an authorized AWS CLI profile and region, and supply the client CIDR for the disposable sandbox. AWS resources incur charges until cleaned up. The dashboard/runner can remain local while the test subject runs on ECS.

## Current scope

The current application-specific repair integration targets the FastAPI demo. Docker Compose supports local reviewed repairs and scoped recovery; AWS ECS supports observations and approved task restoration. Kubernetes/Azure adapters and automatic AWS rollout of newly AI-repaired images are not implemented. Connect another app by supplying its tests and supported adapter integration; a URL alone does not create a safe repair strategy.
