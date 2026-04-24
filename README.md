# Onward Journey (AWS Hosted)

## Summary
A prototype hosted on AWS for connecting users to further support after a chat session can't produce a satisfactory answer.

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
Copy the example config to create your target environment configuration. *You should replace `environment_name` with something appropriate e.g. your initials, 'dev', etc.*:
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

We use the gds cli to assume roles on our development machines, for a list of relevant roles see `gds aws | grep onwardjourney`. You use one of these roles when working with terraform by running e.g. `gds aws <role-name> -- terraform plan`, or you can run `gds aws <role-name> -- $SHELL` to start a new shell session authenticated as the relevant role.

### Phase 1: The Foundation (VPC)
**Note:** You only need to run this phase if you are deploying to a **new AWS account** or have **changed the shared network infrastructure**.

**CRITICAL:** This layer MUST be deployed using the **`default`** Terraform workspace.

```bash
# n.b. You can check that you are in the correct workspace by listing out existing workspaces. An asterisk will be displayed next to the selected workspace.
terraform workspace list
```

```bash
cd infrastructure/vpc

# 1. Initialise - pointing to your config
terraform init -reconfigure -backend-config="../environments/<environment_name>.config"


# 2. Plan - To view what changes your terraform code will make to the 'default' workspace
terraform plan

# 3. Deploy to the 'default' workspace
terraform apply

# N.B. You can check that you are in the correct workspace by listing out existing workspaces
terraform workspace list
```

### Phase 2: Your Developer Workspace (Services)
Most daily development happens here. This layer uses **individual developer workspaces** for isolation.

```bash
cd infrastructure/services

# 1. Initialise - pointing to your config
terraform init -reconfigure -backend-config="../environments/<environment_name>.config"

# 2. Select or Create your unique workspace. For development, replace `workspace_name` with your initials
terraform workspace select <workspace_name> || terraform workspace new <workspace_name>

# 3. Plan - To view what changes your terraform code will make
terraform plan

# 4. Apply - If you are happy with the changes
terraform apply
```

> [!IMPORTANT]
> Your first `services` apply will partially fail when creating the RDS instance. This is expected "Secure-by-Default" behaviour. Follow the **[Developer Quick Start](infrastructure/README.md#developer-quick-start)** in the Infrastructure README to manually set your passwords.
