import { defineConfig } from '@playwright/test';

const PORT = 4173;

export default defineConfig({
  testDir: './tests',
  testMatch: ['a11y/**/*.test.ts', 'e2e/**/*.spec.ts'],
  fullyParallel: true,
  reporter: 'line',
  // PW_CHANNEL=msedge|chrome runs an installed browser when the bundled Chromium is unavailable.
  use: { baseURL: `http://localhost:${PORT}`, channel: process.env.PW_CHANNEL },
  // Serve the production build against the fixture-backed mock client.
  webServer: {
    command: `npm run build && npx vite preview --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: { VITE_USE_MOCK: '1', VITE_MOCK_FIXTURE: 'review' },
  },
});
