# Phase 26 — Connect an AWS test subject

## What this part of the machine does

CloudShield remains a separate project. An adapter translates its observation and recovery operations into provider-specific calls. The first cloud adapter supports AWS ECS on Fargate. Docker Compose remains the local adapter. Kubernetes and Azure adapters are future work, not capabilities supplied by the ECS adapter.

The supplied application is a test subject, not part of CloudShield. This lesson deploys that subject into your own disposable AWS account environment and saves a target manifest for CloudShield. An existing application can instead supply the same resource contract; it does not have to be redeployed with this template.

## The technologies

* **ECR:** stores the backend image. CloudShield records an immutable image digest, rather than trusting a movable tag.
* **ECS:** manages the service and its desired number of tasks. A task definition records the container image, environment, health checks and launch settings.
* **Fargate:** supplies compute for the task without managing an EC2 host.
* **CloudFormation:** creates the disposable network, registry, service, logging and secret resources from JSON templates.
* **Secrets Manager:** supplies generated lab credentials directly to the task. Credentials are not written to a committed manifest.
* **IAM:** grants the execution role permission to pull the image, retrieve its particular secrets and write its logs. The CloudShield operator profile has separate observation/recovery permissions.
* **CloudWatch Logs:** receives the backend and database logs. The adapter saves aggregate error/timeout counts from the backend stream, not raw messages.

The sandbox runs backend and PostgreSQL containers inside one task. They communicate over the task's loopback interface. This is an inexpensive, disposable demonstration shape, not a production database architecture. Task replacement recreates its synthetic database. The public API port is restricted to your explicitly supplied client IPv4 `/32`; there is no load balancer or TLS in this lab. Do not use real user data.

## Before the lab

Install the official AWS CLI v2, log into an authorized profile (prefer your account's SSO workflow), and confirm `aws --profile YOUR_PROFILE sts get-caller-identity`. Docker Desktop must be running and accessible. Use a clean pinned FastAPI checkout. These commands create billable AWS resources; remove the lab when finished. Region, profile and client CIDR are account-specific and cannot be inferred from a local Docker deployment.

From the CloudShield folder, substitute your actual profile, region and public client IPv4:

```powershell
./aws/deploy.ps1 -Profile YOUR_PROFILE -Region us-east-1 -ClientCidr YOUR_PUBLIC_IPV4/32
```

The script creates the registry, builds/pushes the exact source revision if not already present, resolves its digest, deploys the sandbox and runs enrollment. It stops on failed native commands. Enrollment refuses to silently overwrite an existing approved baseline.

If deployment already completed but enrollment needs retrying:

```powershell
& ./.venv/Scripts/python.exe -m cloudshield aws enroll --stack cloudshield-lab --profile YOUR_PROFILE --region us-east-1 --output runs/aws/cloudshield-lab/target.local.json
```

## What the connection contains

Open the generated `runs/aws/cloudshield-lab/target.local.json`. It records `adapter`, `profile`, `region`, full cluster/service/task-definition ARNs, approved image digest, desired count, source revision, log group and synthetic secret reference. It contains no AWS access keys. AWS CLI uses its existing credential chain. CloudShield validates region and account consistency before invoking operations.

`allow_restore` starts false. This phase registers observation; phase 27 explicitly enables recovery. `probe_health` controls FastAPI health and anonymous-access checks. `synthetic_login` optionally verifies actual database-backed login; it starts false because that HTTP probe is specific to disposable data. Enable it for this sandbox to demonstrate the DB_HOST fault in phase 27.

To connect an existing ECS service, create a manifest in `examples/` using the same fields and set `sandbox: false`. Supply its approved full ARNs and backend image digest, and `desired_count: 1`. The current API probes expect this FastAPI contract, backend container name and public task port 8000; set `probe_health: false` for infrastructure-only observations of other ECS apps. Such an observation does **not** verify application behavior. Custom app tests and private/TLS endpoint support require another app integration. The sandbox fault injector is unavailable for ordinary apps.

## Operator permissions

Observation needs `ecs:DescribeServices`, `ecs:ListTasks`, `ecs:DescribeTasks`, and (for HTTP probes) `ec2:DescribeNetworkInterfaces`. Optional log aggregates need `logs:GetLogEvents` scoped to the registered log streams. Synthetic login needs `secretsmanager:GetSecretValue` only for the registered admin secret. Restoration adds `ecs:UpdateService` only on the registered service, with its cluster condition. Fault injection additionally needs task-definition registration/description and `iam:PassRole` only for that task's execution role. Deployment needs CloudFormation and the services referenced by these templates. Use a separate authorized lab provisioning profile; CloudShield's running observer does not need all provisioning permissions.

## Understand before moving on

Explain the difference between an image in ECR, a task definition, a running Fargate task, and an ECS service. Explain why CloudShield needs access to the subject's AWS resources but does not need to run inside its application container.

Sources: [ECS service updates](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_UpdateService.html), [ECS execution roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-iam-roles.html).
