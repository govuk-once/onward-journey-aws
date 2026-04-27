# Onward Journey (AWS Hosted)

## Summary
A prototype hosted on AWS for connecting users to further support after a chat session cannot produce a satisfactory answer.

This project uses a **Layered Infrastructure** model:
* **The Foundation (`infrastructure/vpc`):** A persistent, shared network (VPC, Subnets, S3 Gateway) managed in the **`default`** Terraform workspace.
* **The Workspaces (`infrastructure/services`):** Isolated developer environments (Lambdas, RDS, Bedrock) managed via **dedicated developer workspaces**.

For detailed information on the infrastructure architecture, environment isolation, and database management, please refer to the [Infrastructure README](infrastructure/README.md).

## Getting started

### Pre-requisites
This project uses [mise-en-place](https://mise.jdx.dev/getting-started.html) to manage runtime versions.
1. Install `mise`.
2. Run `mise activate` from the root of this repo or [set up your shell to automatically activate mise on startup](https://mise.jdx.dev/getting-started.html#activate-mise).
3. Run `mise install`.
4. Install tools from the [laptop-configuration repo](https://github.com/govuk-once/laptop-configuration).
5. Ensure the GDS CLI is installed and configured; verify by running `gds aws once-onwardjourney-development-readonly -- echo "test"`.
6. Install pre-commit hooks: `pre-commit install`. This will run the hooks listed in `.pre-commit-config.yaml` before each commit.

## 1. Initial Configuration

Before deploying, you must create your environment configuration files.

**Note: Do not manually type the backend paths (bucket/key) into the CLI prompts.** Always use the configuration file to ensure environment isolation.

### Create your Backend Config
Copy the example config to create your target environment configuration. *Replace `environment_name` with something appropriate, e.g. your initials or 'dev':*
`cp infrastructure/environments/.example.config infrastructure/environments/<environment_name>.config`

Update `<environment_name>.config` with your AWS Account ID:
```hcl
region               = "eu-west-2"
bucket               = "govuk-once-onwardjourney-dev-<AWS account ID>-tfstate"
use_lockfile         = true
encrypt              = true
key                  = "onward-journey.tfstate"
workspace_key_prefix = "environment"
```

### Create your Local Variables
You must set your environment name and account ID in `infrastructure/services/local.auto.tfvars`:
```hcl
environment    = "<your initials>"
aws_account_id = "<AWS account ID>"
```


## 2. Deploying Infrastructure

We use the GDS CLI to assume roles on our development machines. To see a list of relevant roles, run `gds aws | grep onwardjourney`. You can use these roles by running e.g. `gds aws <role-name> -- terraform plan`, or start a new authenticated shell session with `gds aws <role-name> -- $SHELL`.

### Phase 1: The Foundation (VPC)
**Note:** You only need to run this phase if you are deploying to a **new AWS account** or have **changed the shared network infrastructure**.

**CRITICAL:** This layer MUST be deployed using the **`default`** Terraform workspace.

```bash
cd infrastructure/vpc

# 1. Initialise - pointing to your config
terraform init -reconfigure -backend-config="../environments/<environment_name>.config"

# 2. Workspace Check - Ensure you are in 'default'. An asterisk will be displayed next to the selected workspace.
terraform workspace list

# 3. Plan - To view what changes your terraform code will make to the 'default' shared network
terraform plan

# 4. Deploy to the 'default' workspace
terraform apply
```

### Phase 2: Your Developer Workspace (Services)
Most daily development happens here. This layer uses **individual developer workspaces** for isolation. Follow these steps for a complete deployment of the services infrastructure:

**1. Initialise and Select Workspace**
```bash
cd infrastructure/services

# Initialise pointing to your config
terraform init -reconfigure -backend-config="../environments/<environment_name>.config"

# Select or Create your unique workspace. For development, replace <workspace_name> with your initials
terraform workspace select <workspace_name> || terraform workspace new <workspace_name>
```

**2. Validate and Prepare Build Folders**
* Run `terraform validate` to ensure your specific environment names meet Bedrock's regex requirements.
* **Build Folders:** The `dist/` directory contains staging folders for Lambda builds. These contain `.gitkeep` files to ensure the directory structure exists for Terraform. **Do not delete these hidden files**, as they are required for the `terraform plan` phase to succeed.

**3. First Deployment (Expected Failure)**
```bash
# View what changes your services terraform code will make
terraform plan

# Execute the first apply
terraform apply
```
> [!IMPORTANT]
> This first `services` apply will partially fail. It will create the Secret containers but will fail at the RDS creation step with a 'couldn't find resource' error. This is expected "Secure-by-Default" behaviour.

**4. Set Passwords in AWS Console**
* **Database Password:** Navigate to **AWS Console** -> **Secrets Manager**. Locate `${var.environment}-dept-contacts-db-password` and set the **plaintext** value. (Ensure no trailing newlines or spaces are included).
* **CRM Secrets:** Navigate to **Secrets Manager** and set the key values for `${var.environment}/crm-creds/*`.

**5. Final Deployment**
Run the apply again to provision the RDS instance and trigger the Seeder.
```bash
terraform apply
```

**6. Integration Testing**
Once the stack is green, follow the [Backend Integration Testing Guide](tests/README.md) to verify the deployment via the CLI.



## Troubleshooting

If you encounter issues with data seeding, Lambda builds, or VPC routing, please refer to the **[Troubleshooting & Maintenance](infrastructure/README.md#troubleshooting--maintenance)** section in the Infrastructure README for detailed recovery steps.
