# API Consumer Project

## Overview

This project is a serverless data pipeline that consumes data from multiple APIs (Soccer and Spotify) and stores the results in AWS S3. It leverages AWS Lambda for computation, AWS Step Functions for orchestration, and Terraform for infrastructure-as-code management.

The application is designed to be scalable, maintainable, and easy to deploy across different environments.

## Project Structure

```
api_consumer/
├── infra/
│   ├── main.tf                      # Main Terraform configuration
│   ├── variables.tf                 # Terraform variables
│   ├── terraform.tfstate            # Terraform state file
│   ├── terraform.tfstate.backup     # Terraform state backup
│   └── definitions/                 # Step Function JSON templates
│       └── soccer_workflow.json.tpl # Soccer workflow definition
│
├── databricks_pipeline/
│   └── soccer_api_consumer/     # Databricks Asset Bundle
│       ├── databricks.yml       # Bundle configuration
│       ├── resources/           # Job and Pipeline definitions
│       └── src/                 # Bundle source code
│
└── src/
    ├── lambda_handler.py            # Lambda entry point
    ├── api_factory.py               # Factory pattern for API clients
    ├── api_clients/
    │   ├── __init__.py
    │   ├── API.py                   # Base API client class
    │   ├── soccer_consumer.py       # Soccer API consumer implementation
    │   └── spotify_consumer.py      # Spotify API consumer implementation
    └── lambda_function.zip          # Packaged Lambda deployment
```

## Features

- **Multi-API Support**: Consume data from Soccer and Spotify APIs
- **Serverless Architecture**: Runs on AWS Lambda with minimal overhead
- **Orchestration**: AWS Step Functions for workflow management
- **Data Persistence**: Store results in AWS S3
- **Infrastructure as Code**: Terraform for reproducible deployments
- **Modular Design**: Factory pattern for extensible API client architecture
- **IAM Security**: Proper role-based access control

## Prerequisites

### Local Development
- Python 3.12+
- Terraform 1.0+
- AWS CLI configured with appropriate credentials
- Git

### AWS Account
- Active AWS account with appropriate permissions
- S3 bucket access
- Lambda execution capabilities
- IAM role creation permissions
- Step Functions permissions

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd api_consumer
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt  # if you have a requirements.txt file
```

Or install individual packages as needed:
```bash
pip install boto3
```

### 3. Configure AWS Credentials

```bash
aws configure
```

Or set environment variables:
```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

### 4. Set Up Terraform Variables

Create a `terraform.tfvars` file in the `infra/` directory:

```hcl
aws_region           = "us-east-1"
api_soccer_api_key   = "your_soccer_api_key"
setup_array_tuple    = ["league1", "league2", "season2024"]
```

**Required Variables:**
- `aws_region`: AWS region for deployment
- `api_soccer_api_key`: API key for soccer data service
- `setup_array_tuple`: Array of leagues and seasons to process

### 5. Deploy Infrastructure

```bash
cd infra
terraform init
terraform plan
terraform apply
```

## Project Components

### Lambda Functions

The Lambda function (`lambda_handler.py`) serves as the entry point for the serverless application. It:
- Receives events from Step Functions
- Instantiates appropriate API clients using the factory pattern
- Processes API responses
- Writes data to S3

### API Clients

**Base Client (`API.py`)**
- Abstract base class for all API consumers
- Defines common interface for API interactions

**Soccer Consumer (`soccer_consumer.py`)**
- Implements soccer/football data API consumption
- Fetches league and match information

**Spotify Consumer (`spotify_consumer.py`)**
- Implements Spotify API data consumption
- Fetches music and artist information

### Step Functions Workflow

The workflow (`definitions/soccer_workflow.json.tpl`) orchestrates:
- Lambda invocation with configurable parameters
- Error handling and retries
- Data transformation between steps

## Configuration

### Environment Variables (Lambda)

Set in `main.tf` under the Lambda environment block:

```hcl
environment {
    variables = {
        TARGET_BUCKET      = aws_s3_bucket.target_bucket.bucket
        API_SOCCER_API_KEY = var.api_soccer_api_key
    }
}
```

### S3 Storage

Data is stored in the `cmurillo-target-bucket` S3 bucket. Bucket policy:
- Write-only from Lambda
- Private ACL
- Force destroy enabled for development

## Deployment Workflow

1. **Local Development**: Test Lambda code locally with mock events
2. **Package**: Terraform automatically zips the `src/` directory
3. **Deploy**: `terraform apply` deploys all resources
4. **Execute**: Trigger Step Function manually or via CloudWatch Events
5. **Monitor**: Check CloudWatch Logs for execution details

## LocalStack Configuration

For local development and testing, this project can be deployed to **LocalStack** - a local AWS cloud emulator.

### Prerequisites

- Docker and Docker Compose installed
- `docker-compose.yml` configured in the project root

### Docker Compose Setup

```yaml
services:
  localstack:
    container_name: localstack_main
    image: localstack/localstack:latest
    ports:
      - "4566:4566"
    environment:
      - DEBUG=1
      - DATA_DIR=/var/lib/localstack/data
    volumes:
      - "local_data:/var/lib/localstack"

volumes:
  local_data:
```

### Starting LocalStack

```bash
docker-compose up -d localstack
```

Verify it's running:
```bash
docker ps | grep localstack
curl http://localhost:4566/
```

### Terraform Configuration for LocalStack

The project uses **Terraform Workspaces** to manage different environments:

#### Test Environment (LocalStack)
```bash
terraform workspace new test
terraform workspace select test
terraform apply
```

Configuration in `locals.tf`:
```hcl
test_config = {
    access_key           = "test"
    secret_key           = "test"
    region               = "us-east-1"
    localstack_endpoint  = "http://localhost:4566"  # LocalStack endpoint
    lambda_layer_arn     = null  # No layers in test
    tags                 = { Environment = "Test" }
}
```

#### Production Environment (AWS)
```bash
terraform workspace new prod
terraform workspace select prod
terraform apply
```

Configuration in `locals.tf`:
```hcl
prod_config = {
    region               = "us-east-1"
    lambda_layer_arn     = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:12"
    tags                 = { Environment = "Production" }
}
```

### Provider Configuration

The AWS provider is configured to use LocalStack when deployed in test workspace:

```hcl
provider "aws" {
    region = local.current.region
    access_key = local.current.access_key
    secret_key = local.current.secret_key
    
    # LocalStack validation skip
    skip_credentials_validation = local.current.localstack_endpoint != null
    skip_metadata_api_check     = local.current.localstack_endpoint != null
    skip_requesting_account_id  = local.current.localstack_endpoint != null
    
    # S3 path-style addressing for LocalStack compatibility
    s3_use_path_style = local.current.localstack_endpoint != null
    
    # LocalStack service endpoints
    endpoints {
        lambda         = local.current.localstack_endpoint
        s3             = local.current.localstack_endpoint
        secretsmanager = local.current.localstack_endpoint
        iam            = local.current.localstack_endpoint
        stepfunctions  = local.current.localstack_endpoint
    }
}
```

### Key LocalStack Configurations

| Setting | Purpose | Value |
|---------|---------|-------|
| `s3_use_path_style` | Disables virtual-hosted-style S3 addressing | `true` for LocalStack |
| `localstack_endpoint` | Routes all AWS API calls to LocalStack | `http://localhost:4566` |
| `skip_credentials_validation` | Bypasses AWS credential validation | `true` for LocalStack |
| `skip_metadata_api_check` | Skips EC2 metadata check | `true` for LocalStack |
| `skip_requesting_account_id` | Skips account ID validation | `true` for LocalStack |

### Lambda Layer Configuration

- **Test (LocalStack)**: `lambda_layer_arn = null` - No external layers
- **Prod (AWS)**: `lambda_layer_arn = "arn:aws:lambda:..."` - Managed layer with Pandas/requests

This avoids layer dependency issues when testing with LocalStack.

### Testing with LocalStack

After deploying to LocalStack:

```bash
# List resources
aws s3 ls --endpoint-url http://localhost:4566

# Invoke Lambda function
aws lambda invoke \
  --function-name api_consumer_lambda \
  --endpoint-url http://localhost:4566 \
  --payload '{"league": 239, "season": 2024}' \
  response.json

# Start Step Function execution
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:000000000000:stateMachine:soccer_data_workflow \
  --endpoint-url http://localhost:4566 \
  --input '{"league": 239, "season": 2024}'
```

### Troubleshooting LocalStack

**Error: "dial tcp: lookup localstack: no such host"**
- LocalStack is not running or not accessible
- Verify with: `docker ps | grep localstack`
- Check endpoint URL in `locals.tf` is correct

**Error: "couldn't find resource" (Secrets Manager)**
- Secret doesn't exist in LocalStack
- Secrets Manager is optional - can be commented out in `main.tf`

**Error: "S3 path style addressing"**
- Ensure `s3_use_path_style = true` is set in provider
- Required for LocalStack S3 compatibility

**Error: "Step Functions validation failed"**
- Ensure `stepfunctions` endpoint is configured in provider
- Validate JSON template syntax in `soccer_workflow.json.tpl`

## Deployment Workflow

1. **Local Development**: Test Lambda code locally with mock events
2. **Package**: Terraform automatically zips the `src/` directory
3. **Deploy**: `terraform apply` deploys all resources
4. **Execute**: Trigger Step Function manually or via CloudWatch Events
5. **Monitor**: Check CloudWatch Logs for execution details

## Usage

### Manual Lambda Invocation

```bash
aws lambda invoke \
  --function-name api_consumer_lambda \
  --payload '{"league": "premier_league", "season": 2024}' \
  response.json
```

### Trigger Step Function

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT_ID:stateMachine:soccer_data_workflow \
  --input '{"league": "premier_league", "season": 2024}'
```

### View Logs

```bash
aws logs tail /aws/lambda/api_consumer_lambda --follow
```

## Development

### Adding New API Consumers

1. Create a new file in `src/api_clients/` (e.g., `new_api_consumer.py`)
2. Inherit from `API.py` base class
3. Implement required methods
4. Update `api_factory.py` to support new API type
5. Test locally before deployment

### Local Testing

```python
from src.api_clients.soccer_consumer import SoccerConsumer

consumer = SoccerConsumer(api_key="your_key")
data = consumer.fetch_data("premier_league", 2024)
```

## Troubleshooting

### Lambda Timeout
- Increase timeout in `main.tf` (`timeout` parameter)
- Check API response times

### S3 Permission Denied
- Verify IAM role policy includes S3 bucket ARN
- Check bucket ACL settings

### Step Function Failures
- View execution history in AWS Console
- Check CloudWatch Logs for error details
- Validate templatefile variables match the template

## Cleanup

To destroy all AWS resources:

```bash
cd infra
terraform destroy
```

**Note**: The `force_destroy = true` on the S3 bucket means all data will be deleted.

## Best Practices

- Store API keys in AWS Secrets Manager (currently commented out in code)
- Use different Terraform states for dev/prod environments
- Enable versioning on S3 buckets for production
- Implement CloudWatch alarms for Lambda errors
- Add VPC configuration if accessing private resources
- Use CloudWatch Events to schedule Step Function executions

## License

not licence at the moment

## Contact

cabymetal@gmail.com
