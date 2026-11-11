import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
  PUBLIC_AWS_REGION: { public: true, static: true },
  PUBLIC_CLIENT_WEBSOCKET_URL: { public: true, static: true },
  PUBLIC_COGNITO_IDENTITY_POOL_ID: { public: true, static: true },
  PUBLIC_WEBSOCKET_AUTH_TOKEN: { public: true, static: true }
});
