import { defineConfig } from 'vite'

export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rollupOptions: {
      onwarn(warning, warn) {
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.id?.includes('/@gravity-ui/uikit/')
          && warning.message.includes('"use client"')) return
        warn(warning)
      },
    },
  },
})
