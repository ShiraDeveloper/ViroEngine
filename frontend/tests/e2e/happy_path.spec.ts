import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

import { exportScreen, review } from '../../src/copy/he';

/** The task's budget for resolving three flagged fields with the keyboard alone. */
const BUDGET_MS = 60_000;

/**
 * Happy path, first half (T21): login -> upload -> live processing -> review.
 * Runs against the fixture-backed mock client.
 */
async function uploadToReview(page: Page): Promise<void> {
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
}

/**
 * Second half (T23): the review fixture has three flagged fields. Two have candidates; the third
 * has none, so it needs manual typing. Everything below uses the keyboard only.
 */
async function resolveThreeByKeyboard(page: Page): Promise<void> {
  await expect(page.getByText(review.flaggedSummary(3), { exact: true })).toBeVisible();

  await page.keyboard.press('Tab');
  await expect(page.locator('td[data-level="D"] button:focus')).toHaveCount(1);
  await page.keyboard.press('1');
  await expect(page.getByText(review.flaggedSummary(2), { exact: true })).toBeVisible();

  await page.keyboard.press('Tab');
  await page.keyboard.press('1');
  await expect(page.getByText(review.flaggedSummary(1), { exact: true })).toBeVisible();

  await page.keyboard.press('Tab');
  await page.keyboard.press('KeyE');
  await expect(page.getByLabel(review.manualLabel)).toBeFocused();
  await page.keyboard.type('1234567');
  await page.keyboard.press('Enter');
  await expect(page.getByText(review.noFlaggedLeft, { exact: true })).toBeVisible();
}

test('login_upload_processing_reaches_review', async ({ page }) => {
  await uploadToReview(page);
});

test('resolve_three_flagged_fields_under_60_seconds', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  await expect(page.locator('tbody tr')).toHaveCount(4);

  const started = Date.now();
  await resolveThreeByKeyboard(page);
  const elapsed = Date.now() - started;

  expect(elapsed, `three fields took ${elapsed} ms`).toBeLessThan(BUDGET_MS);
});

test('evidence_lens_follows_the_selected_cell', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('lens-frame')).toHaveAttribute('data-box', /.+/);
  const first = await page.getByTestId('lens-frame').getAttribute('data-box');
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('lens-frame')).not.toHaveAttribute('data-box', first ?? '');
});

test('full_flow_upload_to_export', async ({ page }) => {
  await uploadToReview(page);
  await resolveThreeByKeyboard(page);

  await page.getByRole('button', { name: review.confirm, exact: true }).click();
  await expect(page).toHaveURL(/\/export$/);
  await expect(page.getByRole('link', { name: exportScreen.xlsx })).toBeVisible();
  await expect(page.getByText(exportScreen.summary(4, 0))).toBeVisible();
});
