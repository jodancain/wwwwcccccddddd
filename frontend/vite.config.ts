import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// NOTE: If running on a network drive (Z: mapped to UNC path with spaces),
// esbuild may fail with path resolution errors. Copy frontend/ to a local
// drive (e.g. C:/WeChatAI_dev/frontend) and run `npm run dev` from there.
// The backend can still run from the network drive.

const backendUrl = process.env.BACKEND_URL || 'http://127.0.0.1:8080'
const backendWsUrl = backendUrl.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      input: 'index.html',
    },
  },
  server: {
    port: 5175,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: backendUrl,
        changeOrigin: true,
      },
      '/ws': {
        target: backendWsUrl,
        ws: true,
      },
    },
  },
})
