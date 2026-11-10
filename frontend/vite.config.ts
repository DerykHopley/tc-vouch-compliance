/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server passes /api to the backend (VOUCH_API, default the local `uvicorn vouch.api:app --port 8000`),
// so the app always calls its own origin, as it will when the backend serves the build.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': process.env.VOUCH_API ?? 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./src/testSetup.ts'] },
})
