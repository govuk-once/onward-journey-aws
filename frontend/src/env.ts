import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
  PUBLIC_CLIENT_WEBSOCKET_URL: { public: true, static: true },
  PUBLIC_WEBSOCKET_AUTH_TOKEN: { public: true, static: true }
});
