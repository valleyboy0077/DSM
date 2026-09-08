import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  root: '.',
  build: {
    outDir: '../src/dsm/frontend',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/info': 'http://localhost:8000',
      '/servers': 'http://localhost:8000',
      '/sensors': 'http://localhost:8000',
      '/fans': 'http://localhost:8000',
    },
  },
});
