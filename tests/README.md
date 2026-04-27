# Backend Integration Testing

This directory contains the utilities required to test the Onward Journey Orchestrator and its associated tools (RDS Search and CRM Handoff) directly via the CLI, bypassing the Svelte frontend.

## Purpose
These tests verify the "Golden Thread" of identity and state persistence. By sending raw JSON payloads to the Orchestrator Lambda, we can validate:
1. RAG Retrieval: Does the AI find the correct department details in RDS?
2. CRM Integration: Does the AI correctly check for agent availability?
3. Session Memory: Does Bedrock AgentCore correctly persist context across multiple turns?

---

## Prerequisites
* AWS CLI configured with active credentials.
* local.auto.tfvars: The script automatically detects your environment prefix from infrastructure/local.auto.tfvars.
* Duplicate the `template_test_event.json` and call the new file `test_event.json`. This is the file you will edit to communicate with the agent during testing.

---

## How to Run
From the root of the repository, run:

```bash
bash tests/test_integration.sh
```

The script will:
* Extract your environment name from your Terraform vars.
* Invoke your specific Orchestrator Lambda (e.g. sw2-orchestrator).
* Save the full AI response to tests/response.json.

---

## Understanding the Payload (test_event.json)

The Orchestrator requires three key fields to manage state:

| Field | Description | Strategy |
| :--- | :--- | :--- |
| message | The user's natural language query. | Change this to test different departments. |
| thread_id | Unique ID for the current chat session. | Keep the same to test follow-up questions. Change to start a fresh chat. |
| actor_id | The unique ID of the citizen/user. | Keep the same for a single dev environment to test identity-based memory isolation. |

---

## Example Scenarios

### 1. The Initial Discovery (RAG and Availability)
Use this to test if the AI can find the DVLA or Home Office in the database and check if they are "Online."

**test_event.json**
```json
{
  "message": "What are the contact details for the Department for renewing my driving licence?",
  "thread_id": "test-session-v89",
  "actor_id": "test-user-089"
}
```

### 2. The Contextual Handoff
Use the same thread_id as the previous test. This verifies that Claude remembers the department you were just talking about and triggers the connect_to_live_chat method.

**test_event.json**
```json
{
  "message": "Yes, please connect me to a live person.",
  "thread_id": "test-session-v89",
  "actor_id": "test-user-089"
}
```

#### Tip for the Handoff test:
When running Example 2, check the response.json for the SIGNAL string. If you see a block starting with `SIGNAL: initiate_live_handoff`, it confirms the backend has successfully prepared the connection parameters for the CRM, verifying the "Switchboard" logic is fully functional.

---

## Artifacts
* response.json: The raw output from the Lambda. This file is ignored by Git to prevent environment-specific data leakage.
* test_integration.sh: The main execution script. It dynamically pulls the environment name from the infrastructure folder.
