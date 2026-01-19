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