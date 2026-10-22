import { expect, test } from '@playwright/test';

const MAX_TABS = 25;

test('dropzone_is_keyboard_accessible', async ({ page }) => {
  await page.goto('/upload');
  const input = page.locator('input[type="file"]');
  await expect(input).toHaveCount(1);

  // Reach the real file input with Tab alone.
  let focused = false;
  for (let i = 0; i < MAX_TABS && !focused; i += 1) {
    await page.keyboard.press('Tab');
    focused = await input.evaluate((element) => element === document.activeElement);
  }
  expect(focused).toBe(true);

  // The visible drop area shows a focus ring (never colour or outline alone removed).
  const label = page.locator('label', { has: page.locator('svg') }).first();
  await expect(label).toHaveCSS('box-shadow', /rgba\(69, 67, 153, 0\.35\)/);

  // Choosing a file works through the same input a keyboard user activates with Enter/Space.
  await input.setInputFiles({ name: 'form.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF') });
  await expect(page.getByRole('button', { name: /form\.pdf/ })).toBeVisible();
});
