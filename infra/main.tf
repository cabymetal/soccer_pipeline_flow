terraform {
  required_providers {
    aws = {
        source = "hashicorp/aws"
        version = "~> 6.0"
    }
  }
}


provider "aws" {
    region = var.aws_region
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
    layers          = [var.lambda_layer_with_pandas]

    timeout = 600 # 10 minutes

    environment {
        variables = {
            TARGET_BUCKET = aws_s3_bucket.target_bucket.bucket
            API_SOCCER_API_KEY = var.api_soccer_api_key
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