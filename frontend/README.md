# Frontend

This directory is a SvelteKit application that serves as an interface for the Onward Journey tool.

Currently, this connects to an Orchestrator service which provides AI responses and can initiate a handoff to a human adviser via Genesys Web Messaging.

## Developing

Navigate to the frontend directory:

```sh
cd frontend
```

Install dependencies with `npm ci` (or `npm install`), then create an `.env` file using `.env.example` as your template.

The values in `.env` should be populated after running a successful `terraform apply` in `infrastructure/services`:

* `PUBLIC_COGNITO_IDENTITY_POOL_ID`: Required to authenticate the connection to the orchestrator (found in the Terraform outputs).
* `PUBLIC_ORCHESTRATOR_URL`: Connects the frontend to the backend orchestration layer. Because the WebSocket connection requires handshake authorisation, you must append your authorisation token as a query parameter to the end of the WebSocket URL.

### Sourcing the Authorisation Token

A secure default token is generated automatically during deployment:

1. Navigate to **AWS Console** -> **Secrets Manager**.
2. Locate the secret named `${var.environment}/websocket/auth-tokens`.
3. In the secret's JSON payload, copy the **key** string (the token UUID).
4. Append this token to the end of `PUBLIC_ORCHESTRATOR_URL` in your `.env` file:

```env
PUBLIC_ORCHESTRATOR_URL=wss://<api_id>.execute-api.eu-west-2.amazonaws.com/<env_prefix>?auth_token=<your-token-uuid>
```

Then, run the development server:

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
**Note on CORS:** When previewing locally, you must run it on port 5173 so the Orchestrator allows the cross-origin request:

```sh
npm run preview -- --port 5173
```
