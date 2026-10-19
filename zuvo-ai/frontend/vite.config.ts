import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server proxies mirror frontend/nginx.conf routing.
const gateway = process.env.VITE_GATEWAY_URL || 'http://127.0.0.1:8000';
const dashboardApi = process.env.VITE_DASHBOARD_API_URL || 'http://127.0.0.1:8001';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api/v1/whatsapp': { target: gateway, changeOrigin: true },
      '/api/v1/messages/simulate-inbound': { target: gateway, changeOrigin: true },
      '/api': { target: dashboardApi, changeOrigin: true },
    },
  },
});
