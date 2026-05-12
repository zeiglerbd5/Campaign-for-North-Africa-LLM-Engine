data "aws_subnets" "default" {
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

output "ecr_url" {
  description = "ECR repository URL (use this for docker tag + push)"
  value       = aws_ecr_repository.engine.repository_url
}

output "s3_bucket" {
  description = "S3 bucket for game saves and logs"
  value       = aws_s3_bucket.runs.bucket
}

output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.engine.name
}

output "task_definition_family" {
  description = "ECS task definition family (use with run-task)"
  value       = aws_ecs_task_definition.engine.family
}

output "log_group" {
  description = "CloudWatch log group"
  value       = aws_cloudwatch_log_group.engine.name
}

output "subnet_ids" {
  description = "Default VPC subnet IDs (use with run-task --network-configuration)"
  value       = data.aws_subnets.default.ids
}
