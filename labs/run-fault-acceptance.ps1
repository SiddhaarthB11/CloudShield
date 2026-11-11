param(
    [string] $JMeterHome = $env:JMETER_HOME,
    [int] $MonitorPort = 18014
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$runner = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $runner '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Set up the CloudShield Python environment first.' }
docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is required to run the live acceptance lab.' }
if (-not $env:GEMINI_API_KEY -and -not (Test-Path (Join-Path $runner '.env.gemini.local'))) {
    throw 'Configure Gemini before running the live AI cases.'
}
if (-not $JMeterHome) { $JMeterHome = Join-Path $runner '../../work/jmeter/apache-jmeter-5.6.3' }
$hasJMeter = (Test-Path -LiteralPath (Join-Path $JMeterHome 'bin/ApacheJMeter.jar')) -and
    ($null -ne (Get-Command java -ErrorAction SilentlyContinue))

function Wait-MonitorStatus($Job, [string] $Status) {
    $deadline = (Get-Date).AddSeconds(150)
    do {
        foreach ($line in @(Receive-Job $Job)) {
            $observation = $line | ConvertFrom-Json
            if ($observation.status -eq $Status) { return $observation }
            if ($observation.status -in @('handoff', 'inconclusive')) {
                throw "Monitor reported $($observation.status). See $($observation.report). $($observation.detail)"
            }
        }
        if ($Job.State -in @('Completed', 'Failed', 'Stopped')) { throw 'The continuous monitor exited early.' }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for monitor status $Status."
}

Push-Location $runner
try {
    $acceptance = Join-Path $runner ('runs/acceptance/' + [guid]::NewGuid().ToString('N').Substring(0, 12))
    New-Item -ItemType Directory -Path $acceptance | Out-Null
    $reportPath = Join-Path $acceptance 'acceptance.json'
    $summary = [ordered]@{ status = 'running'; cases = @(); continuous = $null }
    $catalog = & $python -m cloudshield fault list | ConvertFrom-Json
    foreach ($scenario in $catalog.scenarios) {
        $name = $scenario.id
        if ($name -eq 'latency' -and -not $hasJMeter) {
            $summary.cases += @{ scenario = $name; status = 'blocked'; detail = 'JMeter or Java is unavailable.' }
            continue
        }
        $options = @()
        if ($name -in @('syntax', 'app-logic', 'authorization', 'login', 'latency')) { $options += '--gemini' }
        if ($name -in @('config-drift', 'dependency')) { $options += '--heal' }
        if ($name -eq 'latency') { $options += @('--jmeter-home', $JMeterHome) }
        Write-Host "Running $name..."
        $json = & $python -m cloudshield fault run $name @options
        if ($json) {
            $result = $json | ConvertFrom-Json
            $summary.cases += @{ scenario = $name; status = $result.status; outcome = $result.outcome
                                 report = $result.report; detail = $result.detail }
            if ($result.cleanup -eq 'failed' -or $result.repair_cleanup -eq 'failed') {
                $summary.detail = 'Stopped after cleanup failed. Remaining cases were not run.'
                break
            }
        } else {
            $summary.cases += @{ scenario = $name; status = 'inconclusive'; detail = 'No fault report was returned.' }
        }
        $summary | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding utf8
    }

    if (-not $summary.detail) {
        $lab = $null
        $job = $null
        $recoveries = @()
        try {
            $lab = & $python -m cloudshield candidate lab examples/target.json --port $MonitorPort | ConvertFrom-Json
            if ($LASTEXITCODE -ne 0 -or -not $lab.project) { throw 'Could not prepare the continuous lab.' }
            & $python -m cloudshield candidate start $lab.project | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not start the continuous lab.' }
            $target = Get-Content -Raw -LiteralPath $lab.manifest | ConvertFrom-Json
            $credentials = Join-Path (Split-Path $lab.manifest) $target.credentials_file
            $database = docker compose --env-file $credentials -f $target.compose_file -p $lab.project ps -q db
            if ($LASTEXITCODE -ne 0 -or "$database" -notmatch '^[a-f0-9]{12,64}$') {
                throw 'Could not identify exactly one database in the disposable project.'
            }
            $job = Start-Job -ArgumentList $runner, $python, $lab.manifest -ScriptBlock {
                param($cwd, $executable, $manifest)
                Set-Location -LiteralPath $cwd
                & $executable -m cloudshield monitor $manifest --recover-stopped-service db --interval 5
            }
            $healthy = Wait-MonitorStatus $job 'healthy'
            foreach ($cycle in 1..2) {
                Write-Host "Continuous monitor cycle ${cycle}: stopping the disposable database..."
                docker stop "$database" | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Could not stop the disposable database.' }
                $restored = Wait-MonitorStatus $job 'recovered'
                if ($restored.recovered_revision -ne $healthy.deployed_revision) {
                    throw 'Recovery changed the source revision.'
                }
                $recoveries += $restored.report
            }
            $summary.continuous = @{ status = 'matched'; recovery_reports = $recoveries }
        } catch {
            $summary.continuous = @{ status = 'inconclusive'; recovery_reports = $recoveries; detail = $_.Exception.Message }
        } finally {
            if ($job) {
                Stop-Job $job -ErrorAction SilentlyContinue
                Remove-Job $job -Force -ErrorAction SilentlyContinue
            }
            if ($lab.project) {
                & $python -m cloudshield candidate discard $lab.project | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    $summary.detail = "Could not clean up continuous lab $($lab.project)."
                    $summary.continuous.status = 'inconclusive'
                }
            }
        }
    }
    $unmatched = @($summary.cases | Where-Object { $_.status -ne 'matched' })
    $summary.status = if ($summary.detail -or $unmatched.Count -or $summary.continuous.status -ne 'matched') {
        'incomplete'
    } else { 'matched' }
    $summary | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $reportPath -Encoding utf8
    $summary.cases | ForEach-Object { [pscustomobject]$_ } | Format-Table scenario,status,outcome,detail -AutoSize
    "Acceptance report: $reportPath"
    if ($summary.status -ne 'matched') { exit 1 }
} finally {
    Pop-Location
}
