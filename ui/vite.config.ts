import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/run':       { target: 'http://localhost:8000', changeOrigin: true },
      '/runs':      { target: 'http://localhost:8000', changeOrigin: true },
      '/events':    { target: 'http://localhost:8000', changeOrigin: true },
      '/artifacts': { target: 'http://localhost:8000', changeOrigin: true },
      '/health':    { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
