$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the Python 3.14 virtual environment and install requirements.txt first.' }
Write-Host 'Open http://127.0.0.1:18700 in your browser.'
Write-Host 'Closing the dashboard does not stop its worker. Use Pause all automation in the UI to stop scheduling.'
& $python -m cloudshield serve
