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

##################################
#        IAM Role & Policy       #
##################################
resource "aws_iam_role" "lambda_exec_role" {
    name = "lambda_exec_role"

    assume_role_policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
            {
                Action = "sts:AssumeRole"
                Effect = "Allow"
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
            }
        ]
    })
  
}

####################################
# Secret Manager to store API Key  #
####################################
# I wont use Secrets Manager in this example, but this is how you would set it up.
# resource "aws_secretsmanager_secret" "api_soccer_secret" {
#    name = "prod/soccer/api"
#    description = "Secret to store API Soccer API Key"
#}

#data "aws_secretsmanager_secret_version" "api_soccer_secret_version" {
#    secret_id = aws_secretsmanager_secret.api_soccer_secret.id
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
    handler       = "lambda_function.lambda_handler"
    runtime       = "python3.12"
    filename      = data.archive_file.lambda_zip.output_path

    source_code_hash = data.archive_file.lambda_zip.output_base64sha256

    environment {
        variables = {
            TARGET_BUCKET = aws_s3_bucket.target_bucket.bucket
            API_SOCCER_API_KEY = var.api_soccer_api_key
        }
    }
  
}

##################################
#       Step Function            #
##################################

resource "aws_sfn_state_machine" "soccer_workflow" {
    name     = "soccer_data_workflow"
    role_arn = aws_iam_role.lambda_exec_role.arn

    definition = templatefile("${path.module}/definitions/soccer_workflow.json.tpl", {
        setup_array_tuple = var.setup_array_tuple,
        lambda_soccer_arn = aws_lambda_function.lambda_function.arn
    })
}