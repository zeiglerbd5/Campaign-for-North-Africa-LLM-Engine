variable "project" {
  description = "Project name; prefix for all resources"
  type        = string
  default     = "cna-engine"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "image_tag" {
  description = "ECR image tag the task definition references"
  type        = string
  default     = "latest"
}

variable "task_cpu" {
  description = "Fargate CPU units (1024 = 1 vCPU)"
  type        = string
  default     = "1024"
}

variable "task_memory" {
  description = "Fargate memory (MB)"
  type        = string
  default     = "2048"
}
