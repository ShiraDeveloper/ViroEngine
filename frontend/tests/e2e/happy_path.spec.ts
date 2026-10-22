import { expect, test } from '@playwright/test';

/**
 * Happy path, first half (T21): login -> upload -> live processing -> review.
 * Runs against the fixture-backed mock client; the second half (review, export) is T23.
 */
test('login_upload_processing_reaches_review', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel(/.+/, { exact: false }).first().fill('demo-code');
  await page.locator('form button[type="submit"]').click();
  await expect(page).toHaveURL(/\/upload$/);

  // The access code lives in sessionStorage only.
  const stored = await page.evaluate(() => ({
    session: sessionStorage.getItem('safeline.accessCode'),
    local: localStorage.getItem('safeline.accessCode'),
  }));
  expect(stored.local).toBeNull();
  expect(stored.session === null || stored.session === 'demo-code').toBe(true);

  await page
    .locator('input[type="file"]')
    .setInputFiles({ name: 'form.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF') });
  await page.locator('section[aria-labelledby="selected-files"] button:not([aria-label])').click();

  await expect(page).toHaveURL(/\/jobs\/[^/]+\/(processing|review)$/);
  await expect(page).toHaveURL(/\/review$/, { timeout: 10_000 });
  await expect(page.locator('tbody tr')).toHaveCount(4);
});
