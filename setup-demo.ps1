param([switch]$Start)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Check-Native { if ($LASTEXITCODE -ne 0) { throw 'Command failed; demo setup stopped.' } }
$targetPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'fastapi-target'
if (-not (Test-Path -LiteralPath $targetPath)) {
    & git clone --branch 0.12.0 --single-branch https://github.com/fastapi/full-stack-fastapi-template.git $targetPath
    Check-Native
}
$revision = & git -C $targetPath rev-parse HEAD
Check-Native
if ($revision.Trim() -ne '84c5a9e11a9e58262599e3d772f5386924e02ca7') { throw 'The demo checkout is not the pinned revision; leave it untouched and use a separate clean checkout.' }
$changes = & git -C $targetPath status --porcelain --untracked-files=all
Check-Native
if ($changes) { throw 'The target checkout has edits. Commit/stash them yourself or use a separate clean checkout; setup will not overwrite them.' }
if (-not (Test-Path -LiteralPath '.env.phase1.local')) {
    @(
        "POSTGRES_PASSWORD=$([guid]::NewGuid().ToString('N'))"
        "APP_SECRET_KEY=$([guid]::NewGuid().ToString('N'))$([guid]::NewGuid().ToString('N'))"
        "APP_ADMIN_PASSWORD=$([guid]::NewGuid().ToString('N'))"
        'APP_HOST_PORT=18020'
        'APP_DB_SECRET_REF=local/postgres'
        "APP_SOURCE_REVISION=$($revision.Trim())"
        ("APP_BUILD_CONTEXT='{0}'" -f $targetPath.Replace('\','/').Replace("'","\'"))
    ) | Set-Content -LiteralPath '.env.phase1.local' -Encoding ascii
}
if (-not $Start) {
    Write-Host 'Separate target checkout and ignored demo environment are ready. Run ./setup-demo.ps1 -Start when Docker is ready.'
    exit 0
}
$compose = @('--env-file','.env.phase1.local','-f','ci/compose.yml','-p','cloudshield-demo')
& docker compose @compose build backend
Check-Native
& docker compose @compose up -d --wait db
Check-Native
& docker compose @compose run --rm --no-deps backend bash scripts/prestart.sh
Check-Native
& docker compose @compose up -d --wait backend
Check-Native
& docker compose @compose ps
Check-Native
Write-Host 'Demo endpoint: http://127.0.0.1:18020. CloudShield dashboard: http://127.0.0.1:18700.'
