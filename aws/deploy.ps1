param(
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9_.@-]+$')][string]$Profile,
    [Parameter(Mandatory)][ValidatePattern('^[a-z]{2}-[a-z]+-\d$')][string]$Region,
    [Parameter(Mandatory)][ValidatePattern('^\d{1,3}(\.\d{1,3}){3}/32$')][string]$ClientCidr,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9-]{0,60}$')][string]$Stack = 'cloudshield-lab'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
function Invoke-AwsJson {
    param([string[]]$Arguments)
    $result = & aws --profile $Profile --region $Region --output json --no-cli-pager @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'AWS operation failed. Check the preceding CLI diagnostic.' }
    if ($result) { return ($result | ConvertFrom-Json) }
}
function Check-Native { if ($LASTEXITCODE -ne 0) { throw 'Native command failed; deployment stopped.' } }
Get-Command aws,docker,git -ErrorAction Stop | Out-Null
$revision = (& git -C ../fastapi-target rev-parse HEAD).Trim()
Check-Native
if ($revision -ne '84c5a9e11a9e58262599e3d772f5386924e02ca7') { throw 'Use the pinned target checkout.' }
$dirty = & git -C ../fastapi-target status --porcelain --untracked-files=all
Check-Native
if ($dirty) { throw 'Use a clean target checkout.' }
Invoke-AwsJson -Arguments @('sts','get-caller-identity') | Select-Object Account,Arn
& aws --profile $Profile --region $Region cloudformation deploy --stack-name "$Stack-registry" --template-file aws/registry.json --no-fail-on-empty-changeset
Check-Native
$registry = Invoke-AwsJson -Arguments @('cloudformation','describe-stacks','--stack-name',"$Stack-registry")
$uri = ($registry.Stacks[0].Outputs | Where-Object OutputKey -eq RepositoryUri).OutputValue
$arn = ($registry.Stacks[0].Outputs | Where-Object OutputKey -eq RepositoryArn).OutputValue
$repository = ($uri -split '/',2)[1]
$existing = Invoke-AwsJson -Arguments @('ecr','list-images','--repository-name',$repository,'--filter','tagStatus=TAGGED')
if (-not ($existing.imageIds | Where-Object imageTag -eq $revision)) {
    # The login token goes directly to Docker stdin, never to a file or argument.
    & aws --profile $Profile --region $Region ecr get-login-password | docker login --username AWS --password-stdin ($uri -split '/')[0]
    Check-Native
    & docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$revision" -f ../fastapi-target/backend/Dockerfile -t "${uri}:$revision" ../fastapi-target
    Check-Native
    & docker push "${uri}:$revision"
    Check-Native
}
$image = Invoke-AwsJson -Arguments @('ecr','describe-images','--repository-name',$repository,'--image-ids',"imageTag=$revision")
$digest = $image.imageDetails[0].imageDigest
if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'ECR did not return an exact image digest.' }
& aws --profile $Profile --region $Region cloudformation deploy --stack-name $Stack --template-file aws/sandbox.json --capabilities CAPABILITY_IAM --parameter-overrides "ImageUri=$uri@$digest" "RepositoryArn=$arn" "SourceRevision=$revision" "ClientCidr=$ClientCidr" --no-fail-on-empty-changeset
Check-Native
& ./.venv/Scripts/python.exe -m cloudshield aws enroll --stack $Stack --profile $Profile --region $Region --output "runs/aws/$Stack/target.local.json"
Check-Native
Write-Host 'AWS sandbox deployed and enrolled. Restoration starts disabled; follow lab 27 to enable it.'
