resource "aws_cloudwatch_log_group" "engine" {
  name              = "/ecs/${var.project}"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "engine" {
  name = var.project
}

resource "aws_ecs_task_definition" "engine" {
  family                   = var.project
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = var.project
      image     = "${aws_ecr_repository.engine.repository_url}:${var.image_tag}"
      essential = true
      environment = [
        { name = "S3_BUCKET", value = aws_s3_bucket.runs.bucket },
        { name = "AWS_DEFAULT_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.engine.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}
