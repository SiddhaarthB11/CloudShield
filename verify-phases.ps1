param([string]$Python = "$PSScriptRoot/.venv/Scripts/python.exe")

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Invoke-CloudShield([int]$Expected, [string[]]$CommandArgs) {
    $json = & $Python -m cloudshield @CommandArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne $Expected) {
        throw "CloudShield $($CommandArgs[0]) returned $exitCode, expected $Expected. $($json -join ' ')"
    }
    return ($json -join "`n" | ConvertFrom-Json)
}

function Invoke-LabDocker([string[]]$CommandArgs) {
    & docker compose @compose @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose $($CommandArgs[0]) failed." }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $composeFile = (Resolve-Path -LiteralPath 'ci/compose.yml').Path
    $envFile = (Resolve-Path -LiteralPath '.env.phase1.local').Path
    $compose = @('--env-file', $envFile, '-f', $composeFile, '-p', 'cloudshield-demo')
    $target = @('--compose', $composeFile, '--env-file', $envFile)

    $reference = Invoke-CloudShield 0 (@('snapshot') + $target + @('--report', 'runs/live-reference.json'))
    $second = Invoke-CloudShield 0 (@('snapshot') + $target + @('--project', 'cloudshield-phase2', '--report', 'runs/live-second.json'))
    if ($reference.DB_HOST -ne 'db' -or $second.DB_HOST -ne 'staging-db') {
        throw 'Expected the two lab environments to use different database hostnames. Check the Phase 2 setup.'
    }
    $null = Invoke-CloudShield 0 @('compare', 'runs/live-reference.json', 'runs/live-second.json',
        '--allow-difference', 'DB_HOST', '--allow-difference', 'DB_SECRET_REF')
    Write-Host 'Phase 2: live environment comparison passed.'

    $baseline = Invoke-CloudShield 0 @('judge', 'examples/target.json', '--report', 'runs/verified-baseline.json')
    if (-not $baseline.deployment.image_id) { throw 'Baseline has no observed build identity.' }
    $null = Invoke-CloudShield 0 (@('evidence') + $target + @('--report', 'runs/verified-evidence.json'))
    Write-Host 'Phases 3–6: static checks, Robot, Judge, and Docker evidence passed.'

    try {
        Invoke-LabDocker @('stop', 'db')
        $outage = Invoke-CloudShield 1 @('judge', 'examples/target.json', '--report', 'runs/verified-outage.json')
        $health = $outage.checks | Where-Object id -eq 'robot/api-health'
        $lookup = $outage.checks | Where-Object id -eq 'robot/database-backed-user-lookup'
        if ($health.status -ne 'pass' -or $lookup.status -ne 'fail') {
            throw 'Expected shallow health to pass and the database-backed check to fail.'
        }
        $evidence = Invoke-CloudShield 0 (@('evidence') + $target + @('--report', 'runs/verified-outage-evidence.json'))
        $database = $evidence.containers | Where-Object service -eq 'db'
        if ($database.state -ne 'exited') { throw 'Expected Docker to report a stopped database.' }
        Write-Host 'Outage: Judge caught the database failure even though shallow health passed.'
    }
    finally {
        Invoke-LabDocker @('up', '-d', '--wait', 'db')
    }

    $null = Invoke-CloudShield 0 @('judge', 'examples/target.json', '--report', 'runs/verified-recovery.json')
    Write-Host 'Recovery passed. Phases 1–6 verified against the live lab. Reports are in runs/.'
}
finally {
    Pop-Location
}
