# Onward Journey - Lambda Application Code

This directory contains Python code for the Onward Journey AWS Lambda functions. The system has been refactored from a local prototype into a modular, AWS-native architecture.

---

## Architecture Overview

The application is structured to support efficient AWS Lambda deployments using a **Shared Lambda Layer** for common dependencies and utility logic.

### Directory Structure

| Path | Description |
| :--- | :--- |
| `lambdas/` | Contains the entry points (`handler.py`) for each individual AWS Lambda function. |
| `lambdas/orchestrator/` | The core **LangGraph State Machine** that coordinates the agent's reasoning and tool calls. |
| `lambdas/rds_seeder/` | Handles S3-to-RDS data ingestion and vector embedding generation. |
| `lambdas/rds_init/` | Idempotently provisions RDS extensions, users (rds_readonly_dept_contacts), and KB tables. |
| `lambdas/kb_sync/` | A **Step Function-driven ETL pipeline** that syncs articles from remote CRMs (e.g. Genesys) into the RDS knowledge base. |
| `lambdas/rds_tool/` | MCP-compatible tool for performing semantic searches against the RDS database. |
| `lambdas/crm_tool/` | MCP-compatible tool for checking human agent availability and initiating handoffs. |
| `shared/utils/` | Common logic (DB connectors, AWS client builders) shared across all Lambdas via the Layer. |

---

## Development & Deployment

### No Local Runtime
**Important:** There is currently no local "interactive" mode or server in this directory. The system is designed to run exclusively within the AWS Lambda environment.

### Deployment via Terraform
Follow the [instructions here to deploy terraform](../README.md#deploying-infrastructure) in the root README.md file.

The build process (defined in `infrastructure/build.tf`) automatically:
*   Creates a **Shared Layer** containing all dependencies (from `pyproject.toml`) and the `shared/utils/` code.
*   Packages each Lambda function into a "thin" zip file containing only its specific handler.

### Shared Logic & Utilities
To maintain consistency and reduce duplication, all common operations should be added to `app/shared/utils/`:
*   `aws.py`: Centralised Boto3 client factory (handles VPC endpoints).
*   `db.py`: RDS/PostgreSQL connection management.

---

## Testing

The test suite is split into fast, offline **Unit Tests** and AWS-dependent **Integration / LLM Judge Tests**.

### Unit Tests
Unit tests run entirely offline and execute quickly without requiring AWS credentials.

To run all unit tests:
```bash
# From the app/ directory
uv run pytest
```

---

### Integration & DeepEval Tests
Integration tests test the routing, tool calling, and output behavior of Lambdas and workflows against AWS services (such as Bedrock LLM judges).

#### Prerequisite
To run integration tests, you must be logged into an authenticated GDS AWS shell from the `app/` directory:

```bash
gds aws <role-name> -- $SHELL
```

#### Pytest
Pytest provides minimal logging, displaying detailed output and judge reasoning only on test failures:

```bash
# Run ALL integration tests
uv run pytest tests/integration

# Run a specific integration test suite (e.g., orchestrator)
uv run pytest tests/integration/orchestrator

# Run a single test function
uv run pytest tests/integration/orchestrator/test_orchestrator_handler.py::test_kb_not_relevant_routes_to_crm_no_live_chat
```

#### DeepEval
DeepEval provides rich terminal output, summarising LLM judge scores and evaluation metrics for both passing and failing tests:

```bash
# Run ALL integration tests with DeepEval
uv run deepeval test run tests/integration

# Run a specific integration test suite (e.g., orchestrator)
uv run deepeval test run tests/integration/orchestrator

# Run a single test function
uv run deepeval test run tests/integration/orchestrator/test_orchestrator_handler.py::test_kb_not_relevant_routes_to_crm_no_live_chat
```

---

### Integration Post-Deployment
Verification of Lambda logic after deployment should be performed using the integration scripts located in the **root** `tests/` directory:

```bash
# From the project root
./tests/test_integration.sh
```
