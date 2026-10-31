import { defineConfig, loadEnv } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        headers: { Authorization: `Bearer ${loadEnv(mode, '.', 'RKA_').RKA_API_TOKEN ?? process.env.RKA_API_TOKEN ?? ''}` },
      },
    },
  },
}));
