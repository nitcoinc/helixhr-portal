import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import frappeui from 'frappe-ui/vite'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    frappeui({
      frontendRoute: '/helixhr',
      buildConfig: {
        // Explicit paths (not auto-inferred): this repo is developed and
        // built outside a real bench checkout, so frappe-ui's bench-folder
        // detection (sites/ + apps/ siblings) is not available here. See
        // U2 in docs/plans/2026-09-02-001-feat-helixhr-portal-phase1-plan.md.
        outDir: '../helixhr/public/helixhr',
        baseUrl: '/assets/helixhr/helixhr/',
        indexHtmlPath: '../helixhr/www/helixhr.html',
        emptyOutDir: true,
        sourcemap: true,
      },
    }),
    vue(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    // Playwright owns tests/e2e/**/*.spec.ts (real browser); vitest's
    // default glob would otherwise also pick those up and crash with
    // "test.describe() not expected here" (confirmed in CI on U3's push).
    exclude: ['tests/e2e/**', 'node_modules/**'],
  },
})
