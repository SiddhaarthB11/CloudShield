param([Parameter(Mandatory)][string]$Profile,[Parameter(Mandatory)][string]$Region,
      [ValidatePattern('^[A-Za-z][A-Za-z0-9-]{0,60}$')][string]$Stack='cloudshield-lab')
$ErrorActionPreference='Stop'
# Explicit cleanup of this disposable lab. Never invoked by the dashboard.
& aws --profile $Profile --region $Region cloudformation delete-stack --stack-name $Stack
if ($LASTEXITCODE -ne 0) { throw 'Sandbox deletion request failed.' }
& aws --profile $Profile --region $Region cloudformation wait stack-delete-complete --stack-name $Stack
if ($LASTEXITCODE -ne 0) { throw 'Sandbox deletion did not complete; inspect CloudFormation.' }
& aws --profile $Profile --region $Region cloudformation delete-stack --stack-name "$Stack-registry"
if ($LASTEXITCODE -ne 0) { throw 'Registry deletion request failed.' }
& aws --profile $Profile --region $Region cloudformation wait stack-delete-complete --stack-name "$Stack-registry"
if ($LASTEXITCODE -ne 0) { throw 'Registry deletion did not complete.' }
Write-Host 'Lab stacks deleted. Secrets Manager secrets may remain pending their recovery-window deletion.'
