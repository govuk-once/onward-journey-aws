# Onward Journey: Infrastructure Overview

This directory contains the terraform code to deploy an instance of the onwards journey application and supporting services.

### The "Shared VPC, Isolated Stack" Model

We utilise a model where foundational networking is **shared** across the account, while application resources are **isolated** per developer environment.

- **How:** A single, persistent **Shared VPC** is provisioned once in the `default` workspace. Individual developer resources (Orchestrators, Databases, etc.) are then deployed into this VPC using dedicated Terraform workspaces.
- **Why:** This approach avoids hitting AWS VPC limits and reduces deployment times by reusing the heavy networking infrastructure. It ensures a consistent network configuration across the team while maintaining total security and data isolation for each developer.

---

### 1. The Shared VPC & Subnets

To simplify connectivity and manage quotas, the networking layer is **shared and persistent**.

- **Shared Resources:** `main-vpc`, `app-pvt-2a/b` (Private), and `dmz-pub-2a` (Public).
- **VPC Endpoints:** We provision interface and gateway endpoints for **Bedrock**, **Secrets Manager**, and **S3**. This allows the isolated stack to communicate with AWS services securely without leaving the private network.
- **Protection:** These resources use the `prevent_destroy = true` lifecycle guardrail to prevent accidental network disruption.
- **⚠️ Tear Down Warning:** If you must destroy the VPC, you must first manually comment out the `prevent_destroy` lines in `infrastructure/vpc/vpc.tf`. **Do not do this unless you are certain no other developer is using the network.**

### 2. Environment Isolation

We use the `var.environment` variable (set in your `local.auto.tfvars`) to prefix resources. This ensures that your work does not conflict with others.

- **Unique Resources:** IAM Roles, Security Groups, RDS Instances, and Bedrock AgentCore modules.
- **Naming Convention:** `[initials]-[resource-name]` (e.g. `ab-orchestrator-sg`).

### 3. Managed Agent Capabilities (AgentCore)

We leverage Amazon Bedrock’s managed "AgentCore" to handle the complexities of the agentic loop:

- **`agent_chat_context`**: This is the managed memory store. It can persist the history handed over from GOV.UK chat and will maintain the ongoing conversation state during the Onward Journey.
- **`tool_interface`**: A standardised gateway using the Model Context Protocol (MCP) that allows the Orchestrator to securely query tools (like the Department Contacts database). **Note:** Inbound traffic is secured via AWS_IAM (SigV4) authentication.

**⚠️ Naming Note:** Due to inconsistent AWS validation rules, Memory resources use underscores (e.g. `agent_chat_context`) while Gateway resources use hyphens (e.g. `tool-interface`).

**Crucial:** Bedrock appends a 10-character unique hash to Memory IDs (e.g. `-yq9yRQ5h7z`). If you need to manually import a memory resource into your state, you must use the full ID found in the AWS Console.

### 4. Database & Secrets Management

The **Government Department Contacts Store** (RDS PostgreSQL 17.6) is our primary knowledge base. It is enhanced with the `pgvector` extension to support semantic search.

- **The "Empty Secret" Workflow:** To maintain security, we do not store the database password in code or state. Consequently, the initial Terraform deployment for an environment will partially fail as it tries to read a secret version that doesn't exist yet.
- **Accessing the DB:** You must manually set your password in **AWS Secrets Manager** after your first deployment.
- **Secret Name:** Look for `${var.environment}-dept-contacts-db-password` in the AWS Console.
- **Connectivity:** Access is strictly controlled via the `${var.environment}-rds-metadata-sg`. It only accepts inbound traffic from the orchestrator, seeder or KB sync pipeline on port 5432.

### 5. Data Ingestion & Database Initialisation

We use a configuration-driven pipeline to manage the database schema and the ingestion of mock datasets.

- **Mock Data Seeding (`rds_seeder`):** Managed via `infrastructure/services/seed_config.yaml`. This is used to wipe and repopulate mock CSV datasets (like department contacts) for PoC testing.
  - **CSV to RDS:** To seed data, add a CSV into `mock_data/` and reference it in the `source_file` field of your table entry in the YAML.
  - **Reset Logic:** The seeder is **destructive**. It uses `DROP TABLE ... CASCADE` to ensure the table state exactly matches the CSV file.
- **Database Initialisation (`rds_init`):** Managed via `infrastructure/services/kb_config.yaml`. This ensures that the database environment is correctly provisioned:
  - **Extensions:** Installs `pgvector`.
  - **Users:** Idempotently creates the `rds_readonly_dept_contacts` SQL user used by the Search Tool.
  - **Security:** Security: Enables AWS IAM token authentication for the rds_readonly_dept_contacts user.
  - **Tables:** Ensures tables used for KB syncs exist without risking data loss. It uses `CREATE TABLE IF NOT EXISTS`.
- **Flexible Primary Keys:** By default, tables are created with a `SERIAL PRIMARY KEY` called `id`. However, you can define your own natural primary key (e.g., `kb_identifier`) in the column list to ensure idempotency and prevent duplicates.
- **Automatic Vectorisation:** If a table defines an `embedding` column and `embedding_source_cols`, the respective Lambda will automatically call Bedrock Titan v2 to generate vectors (for the seeder) or provision the correct vector types (for RDS init).
- **Automatic Triggers:** Terraform monitors YAML configs, Lambda code, and mock CSV files. Any change will automatically trigger the appropriate Lambda (`rds_seeder` for mock data, `rds_init` for RDS infra and user sync).

---

### 6. Managing the Read-Only User

The `rds_tool` Lambda is strictly restricted to read-only access using a dedicated SQL user called `rds_readonly_dept_contacts`.

This uses AWS IAM Database Authentication. The Lambda does not use a static password or access Secrets Manager for database connectivity. Instead, it generates a short-lived AWS IAM token at runtime to authenticate. Local permissions within PostgreSQL are restricted to SELECT operations only, ensuring a zero-leak state configuration.
    ```

---

### 7. Knowledge Base ETL Sync Pipeline (Step Functions)

In addition to the static seeder, we have a dynamic **Knowledge Base Sync Pipeline** designed to fetch and vectorise articles from remote CRM platforms (e.g., Genesys Cloud) on a schedule.

- **Orchestration:** Managed via an AWS Step Function (`kb-sync-machine`).
- **Workflow Steps:**
    1.  **CheckKBMeta:** Polls the remote CRM to get the `dateModified` of the target Knowledge Base.
    2.  **CheckSyncMeta:** Compares the remote timestamp with the `last_modified` date stored in the local `sync_kb_metadata` table.
    3.  **Choice (IsSyncRequired):** If the dates match, the execution ends. If they differ, the sync proceeds.
    4.  **FetchArticles:** Downloads all articles from the remote CRM.
    5.  **ProcessAndEmbedArticles (Map State):** Iterates through each article in parallel, calling Bedrock Titan v2 to generate embeddings and upserting the results into the `knowledge_base_articles` table.
- **Scheduling:** EventBridge rules are dynamically created based on the `active_pipelines` configuration in the Terraform locals.
- **Observability:** Logs for each step are available in CloudWatch under `/aws/lambda/[initials]-kb-sync-*` and the Step Function execution history.

---

### Troubleshooting & Maintenance

#### When the Seeder or Init Fails
If your tables or data aren't appearing in RDS after an apply, check the following:

1.  **CloudWatch Logs:**
    Look for "Connection Timeout" or Bedrock "Access Denied" errors:
    *   Mock Data: `/aws/lambda/[initials]-rds-seeder`
    *   RDS Infrastructure: `/aws/lambda/[initials]-rds-init`
2.  **VPC Routing:** Ensure the S3 Gateway Endpoint is associated with the private route table in the VPC layer.
3.  **Secrets Format:** Ensure the DB password in Secrets Manager is saved as a **Plaintext** string, not a JSON key-pair.

#### Manual Refresh
To force-rebuild a mock table or re-run the RDS initialisation:

```bash
# Force-rebuild a mock table (e.g., dept_contacts)
terraform apply -replace='terraform_data.rds_sync_trigger["dept_contacts"]'

# Re-run RDS infrastructure and user initialisation
terraform apply -replace='terraform_data.rds_init_trigger'
```

#### Manual Knowledge Base sync

The Knowledge Bases may require manual syncing after applying terraform - to check if this is the case, go to the `rds-tool` lambda in AWS console and run a test command for each `kb_identifier` (`hmp-track-001`, `dvla-renew-003`, `ho-visa-005`), for example:

```
{
  "method": "query_knowledge_base",
  "arguments": {
    "query": "how do i track my personal travel document application?",
    "kb_identifier": "hmp-track-001"
  }
}
```
The query should be successful, but check the details - if the result content only displays an empty array or unexpected content, you will need to manually sync. Follow these steps:

1: Ensure tables are provisioned
  If the tables `sync_kb_metadata` or `knowledge_base_articles` are missing, run:
  ```bash
  terraform apply -replace='terraform_data.rds_init_trigger'
  ```

2: Manually run the sync pipeline
  Go to step functions in AWS console and select the `kb-sync-machine` prefixed with your initials. Click start execution and run the following:
  ```
  {
    "kb_identifier": "hmp-track-001",
    "platform": "genesys",
    "sync_type": "scheduled"
  }
  ```
  You will need to run this an additional two times, replacing the `kb_identifier` with `dvla-renew-003` and `ho-visa-005` respectively.

  Now go to `rds-tool` lambda and run a `query_knowledge_base` test for each `kb_identifier` - you should now see the correct information in the result content.

#### Manual Build/Rebuild (Lambda Packaging)
If a Lambda build is unsuccessful or staging folders are corrupted, use the `taint` command.

```bash
# Force a rebuild of the Orchestrator package
terraform taint null_resource.install_orchestrator_deps
```

Or to rebuild everything at once, you can run:

```bash
# Force a rebuild of all packages
terraform apply \
  -replace="null_resource.install_orchestrator_deps" \
  -replace="null_resource.install_seeder_deps" \
  -replace="null_resource.install_rds_tool_deps" \
  -replace="null_resource.install_crm_tool_deps"
```
