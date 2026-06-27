import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  // Both the dev server and `vite preview` sit behind the Caddy reverse proxy, which
  // forwards the public domain (e.g. onelifegame.duckdns.org) as the Host header. Vite
  // 5 rejects unknown hosts by default; only Caddy can reach this port (it isn't
  // published), so allow any host.
  // `usePolling` so Vite's HMR detects host edits through the Docker bind mount —
  // on Windows/macOS Docker Desktop, native fs events don't cross into the Linux
  // container, so without polling a saved file never triggers a reload.
  server: { host: '0.0.0.0', port: 5173, allowedHosts: true, watch: { usePolling: true } },
  preview: { host: '0.0.0.0', port: 5173, allowedHosts: true },
});
