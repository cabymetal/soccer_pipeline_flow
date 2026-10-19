terraform {
  required_providers {
    aws = {
        source = "hashicorp/aws"
        version = "~> 6.0"
    }
  }
}


provider "aws" {
    region = local.current.region
    access_key = local.current.access_key
    secret_key = local.current.secret_key
    # Only required for LocalStack to skip validations
    skip_credentials_validation = local.current.localstack_endpoint != null
    skip_metadata_api_check     = local.current.localstack_endpoint != null
    skip_requesting_account_id  = local.current.localstack_endpoint != null
    # Force path-style addressing for S3 with LocalStack
    s3_use_path_style = local.current.localstack_endpoint != null
    endpoints {
        lambda         = local.current.localstack_endpoint
        s3             = local.current.localstack_endpoint
        secretsmanager = local.current.localstack_endpoint
        iam            = local.current.localstack_endpoint
        stepfunctions  = local.current.localstack_endpoint
    }

}


##################################
#           S3 Bucket            #   
##################################
resource "aws_s3_bucket" "target_bucket" {
    bucket = "cmurillo-target-bucket"
    force_destroy = true

    tags = {
        Name        = "target-bucket"
        Environment = "Dev"
    }
  
}

resource "aws_s3_bucket_ownership_controls" "target_bucket_ownership_controls" {
    bucket = aws_s3_bucket.target_bucket.id

    rule {
        object_ownership = "BucketOwnerPreferred"
    }
  
}

resource "aws_s3_bucket_acl" "target_bucket_acl" {
    depends_on = [ aws_s3_bucket_ownership_controls.target_bucket_ownership_controls ]

    bucket = aws_s3_bucket.target_bucket.id
    acl    = "private"
}

####################################
# Secret Manager to store API Key  #
####################################
# I wont use Secrets Manager in this example, but this is how you would set it up.
data "aws_secretsmanager_secret" "api_soccer_secret" {
    name = "prod/soccer/api"
}

#data "aws_secretsmanager_secret_version" "api_soccer_secret_version" {
#    secret_id = aws_secretsmanager_secret.api_soccer_secret.id
#}

##################################
#        IAM Role & Policy       #
##################################
resource "aws_iam_role" "lambda_exec_role" {
    name = "lambda_exec_role"

    assume_role_policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
            {
                Effect = "Allow"
                Action = "sts:AssumeRole"
                Principal = {
                    Service = "lambda.amazonaws.com"
                }
            },
        ]
    })
}

resource "aws_iam_role_policy" "lambda_s3_write_policy" {
    name = "lambda_s3_write_policy"
    role = aws_iam_role.lambda_exec_role.id

    policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
            {
                Action = [
                    "s3:PutObject",
                    "s3:PutObjectAcl"
                ]
                Effect   = "Allow"
                Resource = "${aws_s3_bucket.target_bucket.arn}/*"
            },
            {
                Action = [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ]
                Effect   = "Allow"
                Resource = "arn:aws:logs:*:*:*"
            },
            {
                Effect   = "Allow"
                Action   = [
                    "secretsmanager:GetSecretValue"
                ]
                Resource = "${data.aws_secretsmanager_secret.api_soccer_secret.arn}" 
            }
        ]
    })
  
}

resource "aws_iam_role_policy" "databricks_s3_access_policy" {
    name = "databricks_s3_access_policy"
    role = aws_iam_role.databricks_s3_role.id
    # Policy to allow Databricks to read from and write to S3 bucket

    policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
            {
                Action = [
                    "s3:GetObject",
                    "s3:ListBucket"
                ]
                Effect   = "Allow"
                Resource = [
                    "${aws_s3_bucket.target_bucket.arn}",
                    "${aws_s3_bucket.target_bucket.arn}/*"
                ]
            },
            {
                Action = [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:DeleteObject",
                    "s3:PutObjectAcl"
                ]
                Effect   = "Allow"
                Resource = "${aws_s3_bucket.target_bucket.arn}/*"
            }
        ]
    })
  
}

resource "aws_iam_role" "databricks_s3_role" {
  name = "databricks-s3-access-role"

  # This allows EC2 instances (Databricks clusters) to assume this role
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "databricks_passrole_policy" {
  name        = "databricks-passrole-policy"
  description = "Allows Databricks cross-account role to pass the databricks-s3-access-role"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.databricks_s3_role.arn
      }
    ]
  })
}

resource "aws_iam_instance_profile" "databricks_instance_profile" {
  name = "databricks-s3-instance-profile"
  role = aws_iam_role.databricks_s3_role.name
}

output "instance_profile_arn" {
  value = aws_iam_instance_profile.databricks_instance_profile.arn
}

output "passrole_policy_arn" {
  value = aws_iam_policy.databricks_passrole_policy.arn
}

##################################
#        Lambda Layer            #
##################################
# this is how to add a layer to a lambda but for pandas is too big for lambda layers
# so I will use a different layer for requests only
# a managed layer could also be used for requests

## resource "null_resource" "install_pandas" {
#    provisioner "local-exec" {
#        command = <<EOT
#        pip install --platform manylinux2014_x86_64 --target=${path.module}/layer/python --implementation cp --python-version 3.12 --only-binary=:all: pandas
#        EOT
#    }

#    triggers = {
#        requirements = filesha256("${path.module}/../requirements.txt")
#    }
#}

#data "archive_file" "layer_pandas_zip" {
#    depends_on = [ null_resource.install_pandas ]

#    type        = "zip"
#    source_dir  = "${path.module}/../src/lambda_layer"
#    output_path = "${path.module}/../src/lambda_layer/lambda_layer.zip"
  
#}

#resource "aws_lambda_layer_version" "data_processing_layer" {
#    layer_name          = "data_processing_layer"
#    filename            = data.archive_file.layer_pandas_zip.output_path
#    compatible_runtimes = ["python3.12"] 
#}


##################################
#         Lambda                 # 
##################################

data "archive_file" "lambda_zip" {
    type        = "zip"
    source_dir = "${path.module}/../src"
    output_path = "${path.module}/../src/lambda_function.zip"
}

resource "aws_lambda_function" "lambda_function" {
    function_name = "api_consumer_lambda"
    role          = aws_iam_role.lambda_exec_role.arn
    handler       = "lambda_handler.lambda_handler"
    runtime       = "python3.12"
    filename      = data.archive_file.lambda_zip.output_path

    source_code_hash = data.archive_file.lambda_zip.output_base64sha256
    layers          = local.current.lambda_layer_arn != null ? [local.current.lambda_layer_arn] : []

    timeout = 600 # 10 minutes

    environment {
        variables = {
            TARGET_BUCKET = aws_s3_bucket.target_bucket.bucket
            API_SOCCER_API_KEY = var.api_soccer_api_key
            API_MAX_PAGES = var.api_max_pages
        }
    }
  
}

##################################
#        Step Function Role      #
##################################

data "aws_iam_policy_document" "step_function_assume_role_policy" {
    statement {
        effect    = "Allow"
        actions = ["sts:AssumeRole"]

        principals {
            type        = "Service"
            identifiers = ["states.amazonaws.com"]
        }
    }
}

resource "aws_iam_role" "step_function_role" {
    name               = "step_function_role"
    assume_role_policy = data.aws_iam_policy_document.step_function_assume_role_policy.json
}

resource "aws_iam_role_policy" "sfn_lambda_invoke" {
  name = "sfn_lambda_invoke_policy"
  role = aws_iam_role.step_function_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        # Just allow invoking the specific Lambda function
        Resource = "${aws_lambda_function.lambda_function.arn}" 
      }
    ]
  })
}

##################################
#       Step Function            #
##################################

resource "aws_sfn_state_machine" "soccer_workflow" {
    name     = "soccer_data_workflow"
    role_arn = aws_iam_role.step_function_role.arn

    definition = templatefile("${path.module}/definitions/soccer_workflow.json.tpl", {
        setup_array_tuple = jsonencode(var.setup_array_tuple),
        lambda_soccer_arn = aws_lambda_function.lambda_function.arn
    })
}