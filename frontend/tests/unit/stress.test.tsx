import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { JobView } from '@/api/types';
import { StressModeButton } from '@/components/StressModeButton';
import type { StressClient } from '@/components/StressModeButton';
import { dashboard } from '@/copy/he';

import { candidate, field, job, row } from './reviewSupport';

const original: JobView = job({
  job_id: 'j1',
  rows: [row('r1', { first_name: field('first_name', 'D', { candidates: [candidate('a')] }) })],
  report: { local_close_rate: 0.8 } as JobView['report'],
});

const degraded: JobView = job({
  job_id: 'j1-stress',
  rows: [
    row('s1', { first_name: field('first_name', 'D'), last_name: field('last_name', 'D') }),
    row('s2', { first_name: field('first_name', 'B') }),
  ],
  report: { local_close_rate: 0.5 } as JobView['report'],
});

function client(overrides: Partial<StressClient> = {}): StressClient {
  return {
    getJob: vi.fn((id: string) => Promise.resolve(id === 'j1' ? original : degraded)),
    stress: vi.fn(() =>
      Promise.resolve({ job_id: 'j1-stress', status: 'needs_review' as const, page_count: 2 }),
    ),
    ...overrides,
  };
}

function renderButton(c: StressClient, timeoutMs?: number) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <StressModeButton jobId="j1" client={c} pollMs={5} timeoutMs={timeoutMs} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openAndConfirm(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: dashboard.stressRun }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: dashboard.stressConfirm }));
}

describe('stress mode', () => {
  it('requires_confirmation_before_running', async () => {
    const c = client();
    renderButton(c);

    fireEvent.click(screen.getByRole('button', { name: dashboard.stressRun }));

    const dialog = await screen.findByRole('dialog');
    expect(c.stress).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: dashboard.stressCancel }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(c.stress).not.toHaveBeenCalled();
  });

  it('shows_before_and_after_counts', async () => {
    const c = client();
    renderButton(c);

    await openAndConfirm();

    const table = await screen.findByRole('table', { name: dashboard.stressResultCaption });
    const rows = within(table).getByText(dashboard.stressMetrics.rows).closest('tr') as HTMLElement;
    expect(within(rows).getAllByRole('cell').map((td) => td.textContent)).toEqual(['1', '2']);
    const flagged = within(table).getByText(dashboard.stressMetrics.flagged).closest('tr') as HTMLElement;
    expect(within(flagged).getAllByRole('cell').map((td) => td.textContent)).toEqual(['1', '2']);
    const local = within(table).getByText(dashboard.stressMetrics.local).closest('tr') as HTMLElement;
    expect(within(local).getAllByRole('cell').map((td) => td.textContent)).toEqual(['80%', '50%']);
    expect(c.stress).toHaveBeenCalledWith('j1', ['nohl', 'rot', 'blur', 'jpeg', 'fax']);
  });

  it('polls_until_the_derived_job_settles', async () => {
    const running: JobView = { ...degraded, status: 'running' };
    let calls = 0;
    const c = client({
      getJob: vi.fn((id: string) => {
        if (id === 'j1') return Promise.resolve(original);
        calls += 1;
        return Promise.resolve(calls < 3 ? running : degraded);
      }),
    });
    renderButton(c);

    await openAndConfirm();

    await screen.findByRole('table', { name: dashboard.stressResultCaption });
    expect(calls).toBeGreaterThanOrEqual(3);
  });

  it('sends_only_the_chosen_ops', async () => {
    const c = client();
    renderButton(c);
    fireEvent.click(screen.getByRole('button', { name: dashboard.stressRun }));
    const dialog = await screen.findByRole('dialog');

    fireEvent.click(within(dialog).getByLabelText(dashboard.stressOps.blur));
    fireEvent.click(within(dialog).getByLabelText(dashboard.stressOps.fax));
    fireEvent.click(within(dialog).getByRole('button', { name: dashboard.stressConfirm }));

    await screen.findByRole('table', { name: dashboard.stressResultCaption });
    expect(c.stress).toHaveBeenCalledWith('j1', ['nohl', 'rot', 'jpeg']);
  });

  it('confirm_is_disabled_with_no_ops', async () => {
    renderButton(client());
    fireEvent.click(screen.getByRole('button', { name: dashboard.stressRun }));
    const dialog = await screen.findByRole('dialog');

    for (const label of Object.values(dashboard.stressOps)) {
      fireEvent.click(within(dialog).getByLabelText(label));
    }

    expect(within(dialog).getByRole('button', { name: dashboard.stressConfirm })).toBeDisabled();
    expect(within(dialog).getByRole('alert')).toHaveTextContent(dashboard.stressNoOps);
  });

  it('reports_an_uninstalled_stress_mode', async () => {
    const c = client({ stress: vi.fn(() => Promise.reject(new ApiError(501))) });
    renderButton(c);

    await openAndConfirm();

    expect(await screen.findByRole('alert')).toHaveTextContent(dashboard.stressUnavailable);
  });

  it('reports_a_timeout_when_the_derived_job_never_settles', async () => {
    const c = client({
      getJob: vi.fn((id: string) =>
        Promise.resolve(id === 'j1' ? original : { ...degraded, status: 'running' as const }),
      ),
    });
    renderButton(c, 30);

    await openAndConfirm();

    expect(await screen.findByRole('alert')).toHaveTextContent(dashboard.stressTimeout);
  });
});
