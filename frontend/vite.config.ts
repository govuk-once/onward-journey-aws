import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vitest/config";
import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [
    sveltekit({
      // Restore $lib alias functionality for SvelteKit 3
      alias: {
        '$lib': 'src/lib'
      },

      // Consult https://svelte.dev/docs/kit/integrations
      // for more information about preprocessors
      preprocess: vitePreprocess(),

      // adapter-auto only supports some environments, see https://svelte.dev/docs/kit/adapter-auto for a list.
      // If your environment is not supported, or you settled on a specific environment, switch out the adapter.
      // See https://svelte.dev/docs/kit/adapters for more information about adapters.
      adapter: adapter({
        pages: 'build',
        assets: 'build',
        fallback: 'index.html',
        precompress: false,
        strict: true
      }),

      compilerOptions: {
        experimental: {
          async: true,
        },
      },
    })
  ],
  css: {
    lightningcss: {
      errorRecovery: true
    },
    preprocessorOptions: {
      scss: {
        loadPaths: ["node_modules/govuk-frontend/dist"],
        quietDeps: true,
        silenceDeprecations: ["import"],
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
