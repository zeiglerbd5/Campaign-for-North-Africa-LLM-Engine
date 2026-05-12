# CNA Engine — AWS Deployment

Containerized deployment of the CNA engine to AWS Fargate, using Bedrock
for inference and S3 for run artifacts. The same engine code that runs
locally against MLX or Ollama runs unchanged in the cloud — only the LLM
backend swaps via `--backend bedrock`.

## Architecture

```
EventBridge (optional) ──► ECS Fargate Task ──► Bedrock (Claude)
                                │             ─► S3 (saves, logs)
                                │             ─► CloudWatch Logs
                                └─ container exits when game ends
```

One Fargate task per game run. No always-on infrastructure; cost stops
when the task exits.

## Prerequisites

- AWS account with permissions for ECR, ECS, S3, IAM, CloudWatch, Bedrock,
  and EC2 read (the default VPC/subnet lookups in `outputs.tf` need
  `ec2:DescribeVpc*` and `ec2:DescribeSubnets`). The managed
  `AmazonEC2ReadOnlyAccess` policy covers this if your user doesn't
  already have it.
- AWS CLI v2 configured (`aws sts get-caller-identity` should succeed)
- Bedrock model access enabled for Anthropic Claude (one-time per account
  via the Bedrock console — fill out the "use case details" form for the
  Claude model row)
- Terraform >= 1.5
- Docker with buildx (for cross-platform builds; needed because Fargate
  runs amd64 and Apple Silicon laptops are arm64)
- Region: `us-east-1` by default

## One-time setup

```bash
cd deploy/aws/terraform
terraform init
terraform apply
```

Creates: ECR repo, S3 bucket (versioned), IAM roles (execution + task),
ECS cluster, Fargate task definition, CloudWatch log group.

Save outputs for the build/run steps:

```bash
export ECR_URL=$(terraform output -raw ecr_url)
export S3_BUCKET=$(terraform output -raw s3_bucket)
export CLUSTER=$(terraform output -raw cluster_name)
export TASK_DEF=$(terraform output -raw task_definition_family)
export SUBNET=$(terraform output -json subnet_ids | jq -r '.[0]')
export REGION=$(terraform output -raw subnet_ids >/dev/null 2>&1; aws configure get region)
```

## Build and push the image

```bash
# From the repo root
cd "$(git rev-parse --show-toplevel)"

# Build for linux/amd64 (Fargate's default arch)
docker buildx build --platform linux/amd64 \
    -t cna-engine:latest \
    -f deploy/aws/Dockerfile .

# Authenticate to ECR
aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "${ECR_URL%/*}"

# Tag and push
docker tag cna-engine:latest "$ECR_URL:latest"
docker push "$ECR_URL:latest"
```

## Run a task

```bash
aws ecs run-task \
    --cluster "$CLUSTER" \
    --launch-type FARGATE \
    --task-definition "$TASK_DEF" \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],assignPublicIp=ENABLED}" \
    --overrides "{
        \"containerOverrides\": [{
            \"name\": \"cna-engine\",
            \"command\": [\"--backend\", \"bedrock\", \"--turns\", \"4\"],
            \"environment\": [
                {\"name\": \"RUN_ID\", \"value\": \"demo-$(date +%s)\"}
            ]
        }]
    }"
```

The task pulls the image, runs the game, syncs `/data` to
`s3://$S3_BUCKET/runs/$RUN_ID/`, and exits. Billing stops on exit.

## Watch progress

```bash
aws logs tail "/ecs/cna-engine" --follow
```

## Verify results

```bash
aws s3 ls "s3://$S3_BUCKET/runs/" --recursive
```

You should see `saves/gtN.json` and `logs/game_*.jsonl` files.

## Local container testing

Verify the container works before pushing:

```bash
# Quick mock run (no API calls)
docker run --rm -v "$(pwd)/local-data:/data" cna-engine:latest \
    --mock --turns 1

# Real run with Anthropic API
docker run --rm \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    -v "$(pwd)/local-data:/data" \
    cna-engine:latest --backend anthropic --turns 1

# Real run with Bedrock (requires AWS creds + model access)
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v "$(pwd)/local-data:/data" \
    cna-engine:latest --backend bedrock --turns 1
```

## Cost envelope

| | Per 4-turn game | Idle |
|---|---|---|
| Fargate (1 vCPU, 2 GB, ~15 min) | ~$0.01 | $0 |
| Bedrock tokens (Sonnet 4.6, ~130k total) | ~$1.00 | $0 |
| S3 (a few MB) | < $0.01 | < $0.01/mo |
| CloudWatch Logs | < $0.01 | < $0.01/mo |
| ECR storage | $0 (first 500 MB free) | $0 |

**Total: ~$1 per game, pennies per month when idle.**

## What this deployment demonstrates

- Containerization (Docker, multi-arch builds)
- IaC with Terraform
- VPC + subnet wiring (default VPC, kept minimal)
- IAM with least-privilege task roles (scoped to specific Bedrock models
  and the run bucket)
- ECR + ECS Fargate (modern serverless containers)
- S3 with versioning and encryption
- CloudWatch Logs with retention
- Bedrock integration via the Converse API (cross-region inference profile)
- Same image runs locally and in the cloud; only the backend flag changes
