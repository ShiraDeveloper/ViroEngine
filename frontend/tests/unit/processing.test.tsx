import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { subscribeResilient } from '@/api/sse';
import type { JobView, SseEvent } from '@/api/types';
import { processing } from '@/copy/he';
import type { ProgressClient } from '@/lib/useJobProgress';
import { Processing } from '@/pages/Processing';

const PAGE_IDS = ['p1', 'p2', 'p3'];

function job(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: 'j1',
    status: 'running',
    expires_at: '2026-10-22T12:30:00+03:00',
    page_count: PAGE_IDS.length,
    pages_done: 0,
    rows: [],
    orphan_page_ids: PAGE_IDS,
    report: null,
    ...overrides,
  };
}

interface Harness {
  client: ProgressClient;
  emit: (event: SseEvent) => Promise<void>;
}

/** A client whose stream the test drives by hand. */
function harness(getJob: ProgressClient['getJob'] = () => Promise.resolve(job())): Harness {
  let listener: ((event: SseEvent) => void) | null = null;
  return {
    client: {
      getJob,
      subscribe: (_jobId, on) => {
        listener = on;
        return () => {
          listener = null;
        };
      },
    },
    // React Query notifies observers on a 0ms timer, so let it fire inside act.
    emit: (event) =>
      act(async () => {
        listener?.(event);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }),
  };
}

function renderProcessing(client: ProgressClient, resilient = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/jobs/j1/processing']}>
        <Routes>
          <Route
            path="/jobs/:jobId/processing"
            element={<Processing client={client} resilient={resilient} />}
          />
          <Route path="/jobs/:jobId/review" element={<p>review-page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function pageEvent(pageId: string, stage: string, status: 'running' | 'done' | 'failed'): SseEvent {
  return { event: 'page', data: { page_id: pageId, label: null, stage, status, pct: 0.5 } };
}

function card(pageId: string): HTMLElement {
  const element = document.querySelector(`[data-page="${pageId}"]`);
  if (!(element instanceof HTMLElement)) throw new Error(`no card for ${pageId}`);
  return element;
}

function pip(pageId: string, stage: string): HTMLElement {
  const element = card(pageId).querySelector(`[data-stage="${stage}"]`);
  if (!(element instanceof HTMLElement)) throw new Error(`no pip ${stage}`);
  return element;
}

afterEach(() => {
  Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
  vi.useRealTimers();
});

describe('processing screen', () => {
  it('renders_one_card_per_page', async () => {
    renderProcessing(harness().client);
    await screen.findByLabelText(processing.countersLabel);
    expect(document.querySelectorAll('[data-page]')).toHaveLength(PAGE_IDS.length);
    expect(within(card('p1')).getAllByRole('listitem')).toHaveLength(11);
  });

  it('stage_pips_light_in_order_from_sse', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    await screen.findByLabelText(processing.countersLabel);
    await emit(pageEvent('p1', 'S2', 'running'));
    expect(['S0', 'S1', 'S2', 'S3'].map((s) => pip('p1', s).dataset.state)).toEqual([
      'done',
      'done',
      'running',
      'pending',
    ]);
    await emit(pageEvent('p1', 'S4', 'running'));
    expect(pip('p1', 'S3').dataset.state).toBe('done');
    // An out-of-order late event never moves a page backwards.
    await emit(pageEvent('p1', 'S1', 'running'));
    expect(pip('p1', 'S4').dataset.state).toBe('running');
    expect(pip('p2', 'S0').dataset.state).toBe('pending');
  });

  it('cloud_stage_pip_uses_cloud_colour', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    await screen.findByLabelText(processing.countersLabel);
    await emit(pageEvent('p1', 'S9', 'done'));
    expect(pip('p1', 'S9')).toHaveAttribute('data-kind', 'cloud');
    expect(pip('p1', 'S9')).toHaveClass('bg-cloud');
    expect(pip('p1', 'S0')).toHaveClass('bg-local');
    expect(pip('p1', 'S0')).not.toHaveClass('bg-cloud');
  });

  it('counters_update_from_sse_events', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    const strip = await screen.findByLabelText(processing.countersLabel);
    await emit({
      event: 'counters',
      data: { pages_done: 2, pages_total: 3, fields_closed_local: 14, crops_sent: 3 },
    });
    expect(within(strip).getByText(processing.kpiLocal).parentElement).toHaveTextContent('14');
    expect(within(strip).getByText(processing.kpiCrops).parentElement).toHaveTextContent('3');
    expect(screen.getByText(processing.progress(2, 3))).toBeInTheDocument();
  });

  it('sse_drop_falls_back_to_polling', async () => {
    const getJob = vi
      .fn<ProgressClient['getJob']>()
      .mockResolvedValueOnce(job())
      .mockResolvedValue(
        job({
          pages_done: 2,
          report: {
            fields_closed_local: 7,
            tier1_calls: 1,
            tier2_calls: 0,
          } as NonNullable<JobView['report']>,
        }),
      );
    const client: ProgressClient = {
      getJob,
      subscribe: (_id, on) => {
        const timer = setTimeout(
          () => on({ event: 'error', data: { code: 'stream_failed', page_id: null, recoverable: true } }),
          0,
        );
        return () => clearTimeout(timer);
      },
    };
    renderProcessing(client, { reconnectDelaysMs: [], pollIntervalMs: 20 });
    const strip = await screen.findByLabelText(processing.countersLabel);
    await waitFor(() =>
      expect(within(strip).getByText(processing.kpiLocal).parentElement).toHaveTextContent('7'),
    );
    expect(screen.getByText(processing.pollingNotice)).toBeInTheDocument();
    expect(screen.getByText(processing.progress(2, 3))).toBeInTheDocument();
  });

  it('keeps_the_review_button_disabled_until_the_scan_is_done', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    await screen.findByLabelText(processing.countersLabel);
    Object.defineProperty(window, 'scrollY', { value: 240, configurable: true });
    fireEvent.scroll(window);
    expect(screen.getByRole('button', { name: processing.goToReview })).toBeDisabled();
    expect(screen.queryByRole('link', { name: processing.goToReview })).toBeNull();
    expect(screen.getByText(processing.reviewWhenDone)).toBeInTheDocument();
    await emit({ event: 'done', data: { status: 'needs_review', flagged: 2, duration_ms: 1000 } });
    expect(screen.getByRole('link', { name: processing.goToReview })).toBeInTheDocument();
    expect(screen.queryByText(processing.reviewWhenDone)).toBeNull();
  });

  it('navigates_to_review_on_done', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    await screen.findByLabelText(processing.countersLabel);
    await emit({ event: 'done', data: { status: 'needs_review', flagged: 2, duration_ms: 1000 } });
    expect(await screen.findByText('review-page')).toBeInTheDocument();
  });

  it('stays_on_page_when_user_has_scrolled', async () => {
    const { client, emit } = harness();
    renderProcessing(client);
    await screen.findByLabelText(processing.countersLabel);
    Object.defineProperty(window, 'scrollY', { value: 240, configurable: true });
    fireEvent.scroll(window);
    await emit({ event: 'done', data: { status: 'needs_review', flagged: 2, duration_ms: 1000 } });
    expect(screen.queryByText('review-page')).toBeNull();
    expect(screen.getByRole('link', { name: processing.goToReview })).toBeInTheDocument();
  });
});

describe('resilient subscription', () => {
  it('reconnects_with_backoff_before_polling', () => {
    vi.useFakeTimers();
    const opened: number[] = [];
    const poll = vi.fn(() => new Promise<JobView>(() => undefined));
    const stop = subscribeResilient(
      {
        open: (on) => {
          opened.push(Date.now());
          on({ event: 'error', data: { code: 'stream_failed', page_id: null, recoverable: true } });
          return () => undefined;
        },
        poll,
      },
      () => undefined,
    );
    vi.advanceTimersByTime(1000 + 2000 + 4000 + 8000 + 8000);
    // First open plus five reconnect attempts, spaced 1s, 2s, 4s, 8s, 8s.
    expect(opened.map((t) => t - (opened[0] ?? 0))).toEqual([0, 1000, 3000, 7000, 15000, 23000]);
    expect(poll).toHaveBeenCalledTimes(1);
    stop();
  });
});
