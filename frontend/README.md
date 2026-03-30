# Frontend

This directory is a sveltekit application that serves as an interface for the Onward Journey tool.

Currently, this connects to an Orchestrator service which provides AI responses and can initiate a handoff to a human advisor via Genesys Web Messaging.

## Developing

Once you've created a project and installed dependencies with `npm ci` (or `npm install`), define the following environment variable in a `.env` file:

```bash
PUBLIC_ORCHESTRATOR_URL="<The URL for the orchestrator lambda>"
```

The `PUBLIC_ORCHESTRATOR_URL` enables the AI-human handoff functionality by connecting the frontend to the backend orchestration layer. Other necessary configuration for human handoff (like Genesys endpoints) is provided dynamically via signals from the orchestrator.

Then, run the server

```sh
npm run dev

# or start the server and open the app in a new browser tab
npm run dev -- --open
```

## Testing

You can run the jest unit tests by running:

```sh
npm run test
```

## Building

To create a production version of your app:

```sh
npm run build
```

You can preview the production build with `npm run preview`.

> To deploy your app, you may need to install an [adapter](https://svelte.dev/docs/kit/adapters) for your target environment.
