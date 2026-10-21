import { createElement } from 'react';
import type { ReactElement } from 'react';

/**
 * Isolate a number, ID or date inside Hebrew text: dir="ltr" + font-mono + tabular digits.
 * Without it "18/06/2026" renders with reordered segments and "X132767" loses its leading letter.
 * Latin content is tagged lang="en" so a screen reader switches voice.
 */
export function ltr(value: string): ReactElement {
  return createElement(
    'span',
    { dir: 'ltr', lang: 'en', className: 'font-mono tabular-nums whitespace-nowrap' },
    value,
  );
}
