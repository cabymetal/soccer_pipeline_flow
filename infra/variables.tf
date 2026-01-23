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

variable "lambda_layer_with_pandas" {
  description = "managed layer that includes pandas and requests library"
  type = string
  default = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:12"
  
}