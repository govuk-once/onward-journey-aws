import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
  PUBLIC_AWS_REGION: { public: true, static: true },
  PUBLIC_ORCHESTRATOR_URL: { public: true, static: true },
  PUBLIC_COGNITO_IDENTITY_POOL_ID: { public: true, static: true }
});
