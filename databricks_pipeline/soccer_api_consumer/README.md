# soccer_api_consumer

This project defines a Databricks Asset Bundle for the Soccer API Consumer pipeline. It ingests data from an external S3 source into Delta tables using Delta Live Tables (DLT).

* `src/`: Python source code for this project.
* `resources/`:  Resource configurations (jobs, pipelines, etc.)

## Databricks Configuration

This project connects directly to an external S3 bucket using an AWS Instance Profile. It does not use Databricks Volumes.

### 1. Setup Instance Profile
1. Retrieve the Instance Profile ARN from your Terraform output:
   ```bash
   cd ../../infra
   terraform output instance_profile_arn
   ```
2. In Databricks, go to **Settings** > **Security** > **Instance Profiles**.
3. Click **Add Instance Profile** and paste the ARN.

### 2. Configure `databricks.yml`
Before deploying, you must update the variables in `databricks.yml` to match your environment.

*   **cluster_id**: The ID of the interactive cluster to use for non-pipeline tasks.
*   **instance_profile_arn**: The ARN configured in Step 1.
*   **source_path**: The S3 URI where data is located (e.g., `s3://<bucket>/`). It has no default: set the `BUNDLE_VAR_source_path` environment variable before `databricks bundle deploy`.

Example `databricks.yml` configuration:
```yaml
targets:
  dev:
    variables:
      cluster_id: "0204-123123-x0ko9ixc"
      instance_profile_arn: "arn:aws:iam::123456789012:instance-profile/databricks-s3-instance-profile"
```

## Deployment

1. Authenticate to your Databricks workspace, if you have not done so already:
    ```
    $ databricks configure
    ```

2. To deploy the bundle (development target):
    ```
    $ databricks bundle deploy --target dev
    ```

3. To run the pipeline:
   ```
   $ databricks bundle run --target dev
   ```
