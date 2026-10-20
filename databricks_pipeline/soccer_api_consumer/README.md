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

### 2. Set the environment variables
`databricks.yml` contains no environment-specific identifiers. Set them as environment variables
(the repo-root `.env` is gitignored and is the place to keep them), then load them before running the CLI:

| Variable | Value |
| --- | --- |
| `DATABRICKS_HOST` | Workspace URL (bundles can't use variables in `workspace.host`) |
| `BUNDLE_VAR_source_path` | S3 URI of the raw data, e.g. `s3://<bucket>` |
| `BUNDLE_VAR_cluster_id` | ID of the interactive cluster used for non-pipeline tasks (dev) |
| `BUNDLE_VAR_instance_profile_arn` | The ARN configured in Step 1 |

```bash
# from the repo root
set -a; . ./.env; set +a
```

The prod deployment path and owner use the deploying user (`${workspace.current_user.userName}`).

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
