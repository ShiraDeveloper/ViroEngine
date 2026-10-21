import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/** The six screens of the app. The job routes use the fixture-backed mock client. */
const ROUTES = [
  '/login',
  '/upload',
  '/jobs/demo/processing',
  '/jobs/demo/review',
  '/jobs/demo/export',
  '/dashboard',
] as const;

test.describe('all_six_routes_have_no_axe_violations', () => {
  for (const route of ROUTES) {
    test(route, async ({ page }) => {
      await page.goto(route);
      await expect(page.locator('h1')).toBeVisible();
      // Wait for query-backed screens to leave their loading state.
      await expect(page.getByRole('status')).toHaveCount(0);

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
    });
  }
});

test('review_route_renders_the_fixture_table_right_to_left', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('tbody tr')).toHaveCount(4);
});
