import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    watch: {
      // Don't watch Rust's build output — cargo locks files there while compiling,
      // which causes Vite's watcher to throw EBUSY on Windows mid-build.
      ignored: ["**/src-tauri/**"],
    },
  },
})
