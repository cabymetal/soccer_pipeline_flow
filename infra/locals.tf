locals {
  environment = terraform.workspace
  
  # Define defaults for all variables
  defaults = {
    region           = "us-east-1"
    bucket_name      = "default-bucket"
    force_destroy    = true
    tags             = {}
    access_key       = null
    secret_key       = null
    localstack_endpoint = null
    lambda_layer_arn = null
  }
  
  # Test environment configuration
  test_config = {
    access_key = "test"
    secret_key = "test"
    region     = "us-east-1"
    localstack_endpoint = "http://localhost:4566"
    tags      = { Environment = "Test" }
    lambda_layer_arn = null  # No lambda layer for test
  }
  
  # Production environment configuration
  prod_config = {
    region           = "us-east-1"
    tags             = { Environment = "Production" }
    lambda_layer_arn = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:12"
  }
  
  # Merge environment-specific config with defaults
  configs = {
    test = merge(local.defaults, local.test_config)
    prod = merge(local.defaults, local.prod_config)
  }
  
  current = local.configs[local.environment]
}