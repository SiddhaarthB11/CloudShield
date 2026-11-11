$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$runner = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $runner '.venv/Scripts/python.exe'

if (-not (Test-Path -LiteralPath $python)) { throw 'Set up the CloudShield Python environment from Phase 4 first.' }
if (-not (Get-Command java -ErrorAction SilentlyContinue)) { throw 'Java is required for JMeter.' }
docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Open Docker Desktop and run this from a PowerShell session that can access it.' }

$jmeterHome = $env:JMETER_HOME
if (-not $jmeterHome) {
    $jmeterHome = Join-Path $runner '../../work/jmeter/apache-jmeter-5.6.3'
}
if (-not (Test-Path -LiteralPath (Join-Path $jmeterHome 'bin/ApacheJMeter.jar'))) {
    $toolsDir = Join-Path $runner '../../work/jmeter'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $file = 'apache-jmeter-5.6.3.zip'
    $archive = Join-Path $toolsDir $file
    $checksum = Join-Path $toolsDir "$file.sha512"
    $release = 'https://archive.apache.org/dist/jmeter/binaries'
    Write-Host 'Downloading Apache JMeter 5.6.3 and checking its SHA-512 checksum...'
    Invoke-WebRequest -Uri "$release/$file.sha512" -OutFile $checksum -UseBasicParsing
    $expected = [regex]::Match((Get-Content -Raw -LiteralPath $checksum), '(?i)\b[a-f0-9]{128}\b').Value
    if (-not $expected) { throw 'Apache did not return a valid SHA-512 checksum.' }
    if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash -ne $expected) {
        Invoke-WebRequest -Uri "$release/$file" -OutFile $archive -UseBasicParsing
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash -ne $expected) {
        throw 'The JMeter archive did not match the Apache checksum.'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $toolsDir -Force
    $jmeterHome = Join-Path $toolsDir 'apache-jmeter-5.6.3'
}

$results = @()
Push-Location $runner
try {
    foreach ($mode in @('normal', 'peak', 'stress', 'compare')) {
        $json = & $python -m cloudshield load $mode --jmeter-home $jmeterHome
        if (-not $json) { throw "CloudShield did not return a report for $mode." }
        $report = $json | ConvertFrom-Json
        foreach ($trial in $report.trials) {
            $results += [pscustomobject]@{
                Profile = $mode
                Workers = $trial.workers
                Status = $trial.status
                P95ms = $trial.metrics.p95_ms
                RequestsPerSecond = $trial.metrics.requests_per_second
                ErrorPct = $trial.metrics.error_pct
                Report = $trial.report
            }
        }
        if ($report.status -eq 'inconclusive') {
            $last = $report.trials[-1]
            Write-Warning "Stopped at $mode. Inspect $($last.report). $($last.detail) $($last.cleanup_detail)"
            break
        }
    }
} finally {
    Pop-Location
}
$results | Format-Table -AutoSize Profile,Workers,Status,P95ms,RequestsPerSecond,ErrorPct
$results | Select-Object Profile,Workers,Report | Format-List
