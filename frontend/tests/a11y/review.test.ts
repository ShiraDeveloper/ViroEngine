import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

test('no_axe_violations', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  await expect(page.locator('tbody tr')).toHaveCount(4);

  // Once with nothing selected, once with a flagged cell open in the Evidence Lens.
  for (const selected of [false, true]) {
    if (selected) {
      await page.keyboard.press('Tab');
      await expect(page.getByTestId('lens-frame')).toBeVisible();
    }
    const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  }
});

test('cells_have_aria_describedby_pointing_at_reason', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  const flagged = page.locator('td[data-level="D"]');
  await expect(flagged).toHaveCount(3);

  for (const cell of await flagged.all()) {
    const id = await cell.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    const reason = page.locator(`[id="${id}"]`);
    await expect(reason).toHaveCount(1);
    await expect(reason).not.toBeEmpty();
  }
});

test('review_is_fully_operable_without_a_mouse', async ({ page }) => {
  await page.goto('/jobs/demo/review');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Slash');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
