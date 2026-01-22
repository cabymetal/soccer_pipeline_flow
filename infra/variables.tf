variable "aws_region" {
  description = "The aws default region to deploy"
  type = string
  default = "us-east-1"
}

variable "api_soccer_api_key" {
    description = "API key for the API Soccer service"
    type        = string
    sensitive   = true
    #just for testing purposes, in real life you would use Secrets Manager
    default = "prod/soccer/api"
}

variable "setup_array_tuple" {
  description = "Tuple to setup array resources"
  type = list(object({league: number, season: number}))
  default = [
    {"league": 140, "season": 2024},
    {"league": 140, "season": 2025},
    {"league": 39, "season": 2024},
    {"league": 39, "season": 2025},
    {"league": 239, "season": 2024},
    {"league": 239, "season": 2025},
    {"league": 239, "season": 2026}
  ]
}