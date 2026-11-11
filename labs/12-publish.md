# Phase 12 — Publish a verified repair branch

Phase 11 checks whether a proposed change is ready to test. Phase 12 gives you a Git branch to review after that candidate passes verification. The branch points to **the same commit CloudShield tested in Docker**. Publishing here means creating a local Git branch and a JSON report.

## How it works

1. Read the saved repair run. Recompute the Verifier decision using the original Judge reports and deployment record. If the run required a Gemini review, match the recorded approval to the exact Git diff.
2. Confirm that the verified candidate commit is a direct child of the failing commit. Confirm that the base branch still points to that failing commit.
3. Ask Git to check the base and create `cloudshield/repairs/<run-id>` in one operation. The publication report links the failing revision, tested commit and image, patch, and check results.

The **base branch** represents the developer's starting point. The **repair branch** is a named pointer to the tested fix. Git already has that commit from the candidate worktree, so publication needs no new commit or image build. Your current checkout and running Docker deployment stay as they were.

## 1. Prepare the lab base branch

From the `cloudshield` folder in PowerShell, load the saved Phase 10 repair. This command can use that run because it passed verification with review disabled. To include a Gemini approval in the publication report, substitute the report path from a verified [Phase 11 run](11-reviewer.md).

```powershell
$run = 'runs/repairs/557ae857cea3/repair.json'
$repo = '../fastapi-target'
$base = 'cloudshield-lab/broken'
$baseline = Get-Content -Raw -LiteralPath (Join-Path (Split-Path $run) 'baseline.json') | ConvertFrom-Json

git -C $repo branch --list $base
```

If that final command prints nothing, create the lab base branch at the saved broken commit. If it already prints a branch, proceed to step 2. The publish command checks its revision for you.

```powershell
git -C $repo branch $base $baseline.deployment.source_revision
```

The demo started from a detached checkout, so this step gives the broken commit a branch name. For a normal project, use the existing branch that actually contained the regression.

## 2. Publish and inspect the repair

```powershell
$publication = python -m cloudshield publish $run --repo $repo --base-ref "refs/heads/$base" | ConvertFrom-Json
$publication | Select-Object status, branch, baseline_revision, candidate_revision, resolved_targets
git -C $repo diff $publication.baseline_revision $publication.candidate_revision
```

Expect `published`, the branch `cloudshield/repairs/557ae857cea3`, and `robot/api-health` under `resolved_targets`. The diff shows `return False` changing to `return True`. Open the `publication.json` path in `$publication.report`. Its `artifacts` entries are relative to that report's folder.

No Docker or Gemini call is needed here. CloudShield uses saved evidence from the verified run. You can run the publish command again for the same run and base. It succeeds if the existing repair branch still points to the verified commit.

## 3. Understand a stale repair

Suppose a teammate adds another commit to the base branch after verification. Running `publish` again against that branch reports that the base advanced. The earlier 39/39 result still describes the original candidate, and the new base needs a fresh repair run. If you're using a remote-tracking base such as `refs/remotes/origin/main`, fetch before checking freshness so the local reference reflects the remote.

The repair branch is a handoff to a maintainer. Pushing a branch, opening a pull request, merging it, and deploying an image are separate actions.

## Explain it back

1. How can the repair branch point to the verified fix without building another image?
2. Which two Git revisions does the publication report tie together?
3. Why should a maintainer ask for new verification if the base branch moves?
