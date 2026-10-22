import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { dashboard } from '../../src/copy/he';

test('no_axe_violations', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0);

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});

test('stress_confirmation_dialog_has_no_axe_violations', async ({ page }) => {
  await page.goto('/dashboard');
  await page.getByRole('button', { name: dashboard.stressRun }).click();
  await expect(page.getByRole('dialog')).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});
