import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Latency Ladder project-wide decision (docs/phase-a/README.md §1.3-1.4): every web rung is
// served cross-origin isolated (5 µs timers, uncoarsened presentation times). A typical
// production Vite app would NOT send these headers.
const crossOriginIsolation = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: { headers: crossOriginIsolation },
  preview: { headers: crossOriginIsolation },
})
