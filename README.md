# CloudShield

CloudShield is an external testing and repair runner. The FastAPI template remains a separate target checkout. The local application integration includes reviewed AI repair, isolated verification and policy-controlled managed recovery. The ECS adapter restores an approved cloud deployment. Other applications require their own tests and supported integration; this is not a universal autonomous repair guarantee.

Start the local dashboard with `./start-dashboard.ps1`, then open `http://127.0.0.1:18700`. Its background worker persists across browser and web-server restarts. Use **Pause all automation** in the dashboard to stop scheduling safely.

## Standalone setup

This repository contains CloudShield only. The optional FastAPI demo app is cloned separately beside it and is not included here. Follow [Getting started](GETTING_STARTED.md) to create your Python environment, launch the dashboard and optionally start the demo. Local secrets and generated repair runs are excluded from Git.

## Checkpoints

| Phase | What to learn | Entry point |
|---|---|---|
| 1 | How the API and Postgres run and communicate | [Run the target app](labs/01-run-target.md) |
| 2 | Expected differences versus configuration drift | [Compare environments](labs/02-compare-environments.md) |
| 3 | Syntax and config checks before startup | [Static checks](labs/03-static-checks.md) |
| 4 | Runtime assertions and reusable Python keywords | [Robot Framework](labs/04-robot-framework.md) |
| 5 | Structured evidence and fail-closed status | [The Judge](labs/05-judge.md) |
| 6 | Read the deployment state, image identity, and selected logs | [Collect evidence](labs/06-evidence.md) |
| 7 | Apply a patch in a separate Git worktree and run the candidate in its own deployment | [Isolate a candidate](labs/07-candidate.md) |
| 8 | Accept a repair only when its exact build passes the regression gate | [Verify a candidate](labs/08-verifier.md) |
| 9 | Run the full repair lifecycle with a known patch and save each attempt | [Repair controller](labs/09-controller.md) |
| 10 | Ask Gemini for a scoped patch and verify it through the same controller | [AI Fixer](labs/10-fixer.md) |
| 11 | Have a separate AI Reviewer assess the exact patch before building | [AI Reviewer](labs/11-reviewer.md) |
| 12 | Publish the verified commit as a local Git branch for review | [Publish a repair](labs/12-publish.md) |
| 13 | Catch a breaking local save and check it in isolation | [Watch local edits](labs/13-watch.md) |
| 14 | Test a pushed Git commit on GitHub Actions and publish a verified repair branch | [Trigger from GitHub](labs/14-github.md) |
| 15 | Detect a running deployment failure on a timer, even without a Git change | [Monitor a deployment](labs/15-monitor.md) |
| 16 | Run repeatable faults and compare the observed outcomes with expectations | [Fault catalog](labs/16-faults.md) |
| 17 | Draw and replay five faults in one isolated lab run | [Plant 5 Breaks](labs/17-plant.md) |
| 18 | Measure latency, throughput, errors, and worker scaling with JMeter | [Performance experiments](labs/18-performance.md) |
| 18 extension | Use repeatable JMeter failures to trigger and verify an isolated repair | [Performance repair](labs/18-performance-repair.md) |
| 19 | Group symptoms and route incidents by cause | [Diagnose and group failures](labs/19-diagnose.md) |
| 20 | Repair saved syntax failures before building Docker | [Syntax repair](labs/20-syntax-repair.md) |
| 21 | Repair more code faults and reuse feedback across attempts | [Code repair and memory](labs/21-code-repair-memory.md) |
| 22 | Start authorized stopped lab containers and confirm recovery | [Service recovery](labs/22-service-recovery.md) |
| 23 | Restore approved configuration and promote verified repairs with rollback | [Managed deployment](labs/23-managed-deployment.md) |
| 24 | Exercise the complete JMeter repair flow | [Performance healing](labs/24-performance-healing.md) |
| 25 | Draw mixed code and deployment faults without a forced health case | [Mixed Plant 5 Breaks](labs/25-mixed-plant.md) |
| 26 | Connect the separate runner to a registered AWS ECS application | [AWS connection](labs/26-aws-connection.md) |
| 27 | Restore an approved AWS deployment and verify recovery | [AWS recovery](labs/27-aws-recovery.md) |
| 28 | Operate persistent jobs, monitoring and repair through the dashboard | [Dashboard](labs/28-dashboard.md) |

Run commands from this `cloudshield` folder in PowerShell unless a lesson says otherwise. Phases 2 and 3 use the Python standard library. Phase 4 uses the pinned package in `requirements.txt` and the running Phase 1 target. Phase 5 combines both.

For local regression tests, run `python -m unittest discover -s tests -v`. These exercise the comparator, syntax checks, Robot report parser, simulated Docker responses, and real HTTP requests against a local test server.

## Current checkpoint

Phases 1–28 have implementation and lesson files. Implementation is distinct from live acceptance: AWS deployment requires an authorized CLI profile and client CIDR, and Docker/JMeter/Gemini end-to-end scenarios require actual local access. See [validation status](VALIDATION.md) for the latest checks and limits. Azure and Kubernetes adapters are not implemented. ECS repair currently restores the approved task definition; deploying newly AI-repaired code to AWS is not included.

The app image uses Python 3.14, so use that version for the target syntax scan and full Judge run. An interpreter mismatch is a runner error. The local build's image label, container ID, and published port let the Judge check which deployment is serving the URL.

## Verify Phases 1–6 together

Start both Compose projects using the Phase 1 and Phase 2 labs. Install Robot in the Python 3.14 environment from Phase 4. From this folder, run `./verify-phases.ps1`. This compares the two running configurations, runs the Judge and evidence collector, stops the Phase 1 lab database, checks that the Judge catches it, and restarts the database even if the outage assertion fails. It confirms recovery and writes reports to `runs/`. Run it only against these disposable lab projects.

The live walkthrough requires Docker access, the pinned Git checkout, and Robot installed. Phase 7 can prepare a Git candidate without Docker. Starting it and verifying the two deployments side by side requires Docker Desktop.
