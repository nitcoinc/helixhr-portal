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
        // P2-U9 step 3 / P2-R24. frappe-ui's build plugin defaults this to
        // true, which published a readable copy of every source file --
        // including the comments that describe this app's authorization
        // seams -- at a guessable URL next to each chunk. Nothing needs
        // them in production: a stack trace from a minified chunk plus the
        // section/action identifier P2-R25 puts in the log is what an
        // incident is actually debugged from. Rebuild locally with
        // `npx vite build --sourcemap` when you do need one.
        sourcemap: false,
      },
    }),
    vue(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      // P2-U9 step 1: 96KB of Feather glyphs this portal never renders,
      // dragged into the eagerly loaded chunk by frappe-ui's Button and
      // Dialog. See src/lib/featherIcons.js for the whole argument and
      // src/lib/featherIcons.test.js for the guard that keeps it true.
      'feather-icons': path.resolve(__dirname, 'src/lib/featherIcons.js'),
    },
  },
  test: {
    // Playwright owns tests/e2e/**/*.spec.ts (real browser); vitest's
    // default glob would otherwise also pick those up and crash with
    // "test.describe() not expected here" (confirmed in CI on U3's push).
    exclude: ['tests/e2e/**', 'node_modules/**'],
  },
})
