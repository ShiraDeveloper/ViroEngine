import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AblationTable } from '@/components/AblationTable';
import { KpiCard } from '@/components/KpiCard';
import { LearningChart } from '@/components/LearningChart';
import { dashboard } from '@/copy/he';
import { formatRate, provenanceOf, silentErrorTone } from '@/lib/metrics';

import {
  breakdown,
  metricsClient,
  renderDashboard,
  report,
  runMetric,
} from './dashboardSupport';

async function renderWith(r = report()) {
  const view = renderDashboard(metricsClient({ available: true, report: r }));
  await screen.findByRole('group', { name: dashboard.kpiGroupLabel });
  return view;
}

function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-kpi]'));
}

describe('metrics dashboard', () => {
  it('silent_error_card_is_first_and_prominent', async () => {
    await renderWith();

    const all = cards();
    expect(all).toHaveLength(6);
    expect(all[0]).toHaveAccessibleName(dashboard.kpi.silent);
    expect(all[0]?.className).toContain('md:col-span-2');
    expect(all.slice(1).every((c) => !c.className.includes('md:col-span-2'))).toBe(true);
    expect(all.map((c) => c.getAttribute('aria-label'))).toEqual([
      dashboard.kpi.silent,
      dashboard.kpi.flagRecall,
      dashboard.kpi.localRate,
      dashboard.kpi.accuracy,
      dashboard.kpi.calls,
      dashboard.kpi.time,
    ]);
  });

  it.each([
    [0.008, 'good'],
    [0.01, 'good'],
    [0.02, 'warn'],
    [0.03, 'warn'],
    [0.05, 'bad'],
    [null, 'neutral'],
  ] as const)('kpi_tone_thresholds_%s', (rate, tone) => {
    expect(silentErrorTone(rate)).toBe(tone);
  });

  it('kpi_tone_is_shown_as_text_and_attribute_not_colour_alone', async () => {
    await renderWith(report({ silent_error: 0.05 }));

    const first = cards()[0];
    expect(first).toHaveAttribute('data-tone', 'bad');
    expect(within(first as HTMLElement).getByText(dashboard.tone.bad)).toBeInTheDocument();
  });

  it('every_card_shows_provenance', async () => {
    await renderWith(report({ split: 'heldout', date: '2026-10-27' }));
    const expected = provenanceOf(report({ split: 'heldout', date: '2026-10-27' }));

    expect(expected).toBe('מדגם סגור · 27.10');
    for (const card of cards()) {
      expect(card).toHaveTextContent(expected);
    }
  });

  it('card_without_provenance_is_not_shown', () => {
    const { container } = render(
      <KpiCard label="x" value="1%" tone="neutral" measuredAt="  " />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('ablation_empty_cells_render_as_dash_not_zero', () => {
    render(<AblationTable reports={[report({ cloud_calls: 0 })]} />);

    const missing = document.querySelector('tr[data-config="no_logo"]') as HTMLElement;
    const cellsText = Array.from(missing.querySelectorAll('td')).map((td) => td.textContent);
    expect(cellsText).toEqual(['—', '—', '—', '—', '—', '—']);
    const measured = document.querySelector('tr[data-config="none"]') as HTMLElement;
    expect(within(measured).getByText('0')).toBeInTheDocument(); // a real zero stays a zero
  });

  it('ablation_null_metric_renders_as_dash', () => {
    render(<AblationTable reports={[report({ handwritten_accuracy: null })]} />);

    const row = document.querySelector('tr[data-config="none"]') as HTMLElement;
    expect(row.querySelectorAll('td')[1]).toHaveTextContent('—');
  });

  it('learning_chart_shows_empty_state_with_one_point', () => {
    render(<LearningChart points={[runMetric(0.61, '2026-10-26T10:00:00Z')]} />);

    expect(screen.getByText(dashboard.learningEmpty)).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('learning_chart_draws_a_line_from_two_points', () => {
    render(
      <LearningChart
        points={[runMetric(0.61, '2026-10-26T10:00:00Z'), runMetric(0.74, '2026-10-26T11:00:00Z')]}
      />,
    );

    expect(screen.getByRole('img')).toHaveAccessibleName(dashboard.learningLabel('61%', '74%'));
  });

  it('field_table_shows_dominant_failure_reason', async () => {
    await renderWith(
      report({
        per_field: [
          breakdown({ field: 'national_id', top_failure_reason: 'check_digit_failed' }),
          breakdown({ field: 'sender', top_failure_reason: null }),
        ],
      }),
    );

    const idRow = document.querySelector('tr[data-field="national_id"]') as HTMLElement;
    expect(idRow.querySelector('[data-reason]')).toHaveTextContent('check_digit_failed');
    const senderRow = document.querySelector('tr[data-field="sender"]') as HTMLElement;
    expect(senderRow.querySelector('[data-reason]')).toHaveTextContent(dashboard.noReason);
  });

  it('dashboard_shows_the_measured_footer', async () => {
    await renderWith();

    expect(screen.getByText(dashboard.measuredNote)).toBeInTheDocument();
  });

  it('dashboard_without_a_report_explains_instead_of_showing_numbers', async () => {
    renderDashboard(metricsClient({ available: false }));

    expect(await screen.findByText(dashboard.noData)).toBeInTheDocument();
    expect(cards()).toHaveLength(0);
  });

  it('formatRate_renders_missing_as_dash', () => {
    expect(formatRate(null)).toBe('—');
    expect(formatRate(0)).toBe('0.0%');
  });
});
