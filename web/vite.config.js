import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: { host: '0.0.0.0', port: 5173 },
  // `vite preview` (production overlay) sits behind the Caddy reverse proxy, which
  // forwards the public domain as the Host header. Vite 5 rejects unknown hosts by
  // default; only Caddy can reach this port (it isn't published), so allow any host.
  preview: { host: '0.0.0.0', port: 5173, allowedHosts: true },
});
