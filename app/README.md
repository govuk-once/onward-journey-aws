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

### Pytest and DeepEval
We have a suite of quasi-integration tests using `pytest` and `DeepEval` which currently test the routing and output behaviour of the Orchestrator lambda. The tests are located in the `tests/orchestrator/test_handley.py` file.

To run these tests, you must be logged into the authenticated GDS AWS shell (`gds aws <role-name> -- $SHELL`).

From the `tests/orchestrator` directory, run either:

`pytest test_handler.py` - this will run the tests with a minimal logging output.

or

`deepeval test run test_handler.py` - this will run the tests and provide comprehensive logging, including a summary of the judge agent's reasoning.

To run a single test, append the test function name as per the following example:

`pytest test_handler.py::test_kb_not_relevant_routes_to_crm_no_live_chat`
`

or

`deepeval test run test_handler.py::test_kb_not_relevant_routes_to_crm_no_live_chat`

### Integration post-deployment
Verification of the Lambda logic should be performed after deployment using the integration tests located in the **root** `tests/` directory:

```bash
# From the project root
./tests/test_integration.sh
```
