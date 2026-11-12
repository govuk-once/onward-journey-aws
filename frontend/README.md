# Frontend

This directory is a SvelteKit application that serves as an interface for the Onward Journey tool.

Currently, this connects to an Orchestrator service which provides AI responses and can initiate a handoff to a human adviser via Genesys Web Messaging.

## Developing

### 1. Install Dependencies

Navigate to the frontend directory and install dependencies:

```sh
cd frontend
npm ci
```

### 2. Configure Environment Variables

Create a local `.env` file using the template:

```sh
cp .env.example .env
```

* `PUBLIC_CLIENT_WEBSOCKET_URL`: The WebSocket endpoint for the backend. Populate this value using the output from a successful `terraform apply` in `infrastructure/services`:
* `PUBLIC_WEBSOCKET_AUTH_TOKEN`: Use the key of the JSON object stored as a secret at `${var.environment}/websocket/auth-tokens`.

#### Sourcing the Authorizer Token

A default token-to-actor mapping is generated as UUIDs during initial deployment:

1. Navigate to **AWS Console** -> **Secrets Manager**.
2. Locate the secret named `${var.environment}/websocket/auth-tokens`.
3. Under the **Secret value** tab, inspect the JSON structure:

   ```json
   {
     "c2d8a4e1-7b3f-4e92-91f8-09a8bc43194a": "e4b1a7d2-3f8c-4a91-b5d6-82f9c3e10a7b"
   }
   ```
   * **Key (left) — Token UUID:** The pre-shared authorisation token (`c2d8a4e1-...`) passed during the WebSocket handshake.
   * **Value (right) — Actor ID UUID:** The internal actor identity (`e4b1a7d2-...`) injected into the connection context by the authorizer. Downstream services (the WebSocket router, DynamoDB sessions, and Bedrock AgentCore) use this ID to attribute user sessions securely, ignoring any identity spoofing attempts in message bodies.

   > **Note on editing and rotation:** Terraform generates these initial UUIDs purely to bootstrap deployment and ignores subsequent changes (`ignore_changes = [secret_string]`). You can freely edit this JSON in Secrets Manager to rotate tokens, update actor mappings, or add additional token/actor pairs for other clients and automated tests without Terraform overwriting your changes.

4. Copy the **key** string and append it as the `auth_token` query parameter to `PUBLIC_CLIENT_WEBSOCKET_URL` in `.env`.

   **Example format:**
   ```env
   PUBLIC_CLIENT_WEBSOCKET_URL=wss://<api_id>[.execute-api.eu-west-2.amazonaws.com/](https://.execute-api.eu-west-2.amazonaws.com/)<env_prefix>?auth_token=<your-token-uuid>
   ```

   Replace all three placeholders with your actual deployment values:
   * `<api_id>`: Your API Gateway ID (found in Terraform outputs).
   * `<env_prefix>`: Your environment name (e.g. `dev`, `staging`, or your personal workspace prefix).
   * `<your-token-uuid>`: The token key copied from Secrets Manager in step 3 (e.g. `c2d8a4e1-7b3f-4e92-91f8-09a8bc43194a`).

### 3. Start the Development Server

Once `.env` is configured, start the local server:

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

## Testing

Run vitest unit tests:

```sh
npm run test
```

## Building

To create a production version of your app:

```sh
npm run build
```

The frontend uses `@sveltejs/adapter-static` and is configured as a Single Page Application.

You can preview the production build locally with `npm run preview`.

> **Note on CORS:** When previewing locally, you must run on port 5173 so the Orchestrator allows cross-origin requests:
>
> ```sh
> npm run preview -- --port 5173
> ```
