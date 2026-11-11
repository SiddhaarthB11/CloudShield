# Phase 14 — Trigger CloudShield from GitHub

Phase 13 watches files on your computer. Phase 14 starts the same repair workflow when you push a commit to a GitHub lab branch. The runner is checked out separately at a pinned commit. Its test suite grades the exact FastAPI commit from the GitHub event.

| Event | What runs |
| --- | --- |
| Push to `cloudshield-lab/**` | Python and configuration checks, a disposable Docker deployment, and the Judge. A supported runtime failure can trigger one Gemini Fixer proposal, a separate review, and candidate verification. A verified fix can become a GitHub repair branch. |
| Pull request | Checks the exact PR head commit. This job uses read-only repository access and runs without Gemini. |

GitHub runs the workflow on a temporary Linux machine. It creates a snapshot environment on port `18004` and, when needed, a repair environment on `18005`. Both use synthetic data. You can close your local watcher before the GitHub run.

## 1. Set up the two GitHub repositories

Create an empty public repo for the CloudShield runner and a separate fork of the public Full Stack FastAPI Template for the demo. Use repositories you own. The public runner lets the workflow check it out with its normal GitHub token. The runner contains the standalone CloudShield code and tests for the public FastAPI lab.

From the `cloudshield` folder, publish the runner if you haven't already. Replace the example owner and repository URL with yours. If you already have a runner repo, commit and push the Phase 14 files there, then use its full commit SHA.

```powershell
git init -b main
git add .gitignore README.md requirements.txt cloudshield ci examples labs performance suites tests verify-phases.ps1
git diff --cached --stat
git commit -m "Add CloudShield through Phase 14"
git remote add origin https://github.com/YOUR-ACCOUNT/cloudshield.git
git push -u origin main
git rev-parse HEAD
```

In the **FastAPI fork**, open **Settings → Secrets and variables → Actions → Variables** and set:

- `CLOUDSHIELD_REPOSITORY` to `YOUR-ACCOUNT/cloudshield`.
- `CLOUDSHIELD_REVISION` to the full runner SHA printed above.

Create a GitHub Actions environment named `cloudshield-lab` under **Settings → Environments** in the FastAPI fork. In that environment, add `GEMINI_API_KEY` as an environment secret. Limit its deployment branches to your trusted lab branch, such as `cloudshield-lab/health-demo`, where available. If Actions is disabled for the fork, enable it from the fork's **Actions** tab. [GitHub environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/managing-environments-for-deployment)

## 2. Install the workflow on the FastAPI lab branch

The workflow template is `ci/target-workflow.yml` in the CloudShield repo. The copy GitHub runs goes in `.github/workflows` in the **FastAPI fork**. Run these commands from CloudShield. Replace the URL with your fork's URL. If the lab checkout or remote already exists, reuse it.

```powershell
$demo = 'cloudshield-lab/health-demo'
$labRepo = Join-Path (Get-Location).Path 'runs/github-lab-target'
git -C ../fastapi-target worktree add -b $demo $labRepo 84c5a9e11a9e58262599e3d772f5386924e02ca7
git -C $labRepo remote add cloudshield-demo https://github.com/YOUR-ACCOUNT/full-stack-fastapi-template.git
New-Item -ItemType Directory -Force -Path (Join-Path $labRepo '.github/workflows') | Out-Null
Copy-Item -LiteralPath 'ci/target-workflow.yml' -Destination (Join-Path $labRepo '.github/workflows/cloudshield.yml')
git -C $labRepo add .github/workflows/cloudshield.yml
git -C $labRepo commit -m "Run CloudShield on lab pushes and pull requests"
git -C $labRepo push -u cloudshield-demo $demo
```

Open the FastAPI fork's **Actions → CloudShield** run. The clean commit should pass. If checkout or setup fails, confirm the pinned runner SHA exists on GitHub and the environment secret is configured.

## 3. Push a real regression

Open the printed file and change the endpoint's final `return True` to `return False`. Save it.

```powershell
Join-Path $labRepo 'backend/app/api/routes/utils.py'
```

Commit and push the broken code:

```powershell
git -C $labRepo add backend/app/api/routes/utils.py
git -C $labRepo commit -m "Lab: break the health endpoint"
git -C $labRepo push cloudshield-demo $demo
git -C $labRepo rev-parse HEAD
```

In the new Actions run, compare the reported `event_revision` with the commit SHA you pushed. If Gemini proposes a fix and it passes review and verification, GitHub creates a branch named `cloudshield/repairs/<run-id>`. The run summary lists the branch. Download **cloudshield-reports** to see the original failure, patch, review decision, and verification result.

**Expect the original push check to stay red if CloudShield found a regression.** The report can show a green candidate with 39 passing checks at the same time. The repair is available as a branch. You can compare it with the lab branch or open a PR to review it. The workflow leaves merging and deployment to the maintainer.

Repair branches fall outside the push trigger. If the lab branch advances during a run, publication checks its revision again before pushing. Recheck freshness when merging. [GitHub Actions workflow triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

## Explain it back

1. Which code does the local watcher see, and which code does the GitHub trigger see?
2. Why does the check on the broken commit stay red when the repair passes?
3. Which event can publish a branch, and why do pull requests run with fewer permissions?
