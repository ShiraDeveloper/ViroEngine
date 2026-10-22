import { Zap } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError } from '@/api/client';
import type { ApiClient } from '@/api/client';
import { dashboard } from '@/copy/he';
import { Button } from '@/design/Button';
import { Dialog } from '@/design/Dialog';
import { Table, TBody, Td, Th, THead, Tr } from '@/design/Table';
import { formatRate } from '@/lib/metrics';
import { ltr } from '@/lib/rtl';
import {
  STRESS_OPS,
  STRESS_POLL_MS,
  STRESS_TIMEOUT_MS,
  isSettled,
  summarizeJob,
} from '@/lib/stress';
import type { JobSummary, StressOp } from '@/lib/stress';

export type StressClient = Pick<ApiClient, 'getJob' | 'stress'>;

export interface StressModeButtonProps {
  /** The job whose pages are degraded. The original stays untouched for comparison. */
  jobId: string;
  client: StressClient;
  /** Injected in tests so polling is fast and deterministic. */
  pollMs?: number;
  timeoutMs?: number;
}

interface Comparison {
  derivedJobId: string;
  before: JobSummary;
  after: JobSummary;
}

type Phase =
  | { kind: 'idle' }
  | { kind: 'confirming' }
  | { kind: 'running' }
  | { kind: 'done'; comparison: Comparison }
  | { kind: 'error'; message: string };

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 501) return dashboard.stressUnavailable;
  if (error instanceof Error && error.message === 'stress-timeout') return dashboard.stressTimeout;
  return dashboard.stressFailed;
}

function pct(rate: number | null): string {
  return formatRate(rate, 0);
}

/** Runs the same degradations as the evaluation set on an existing job, behind a confirmation,
 * and shows the original and the degraded job side by side. */
export function StressModeButton({
  jobId,
  client,
  pollMs = STRESS_POLL_MS,
  timeoutMs = STRESS_TIMEOUT_MS,
}: StressModeButtonProps) {
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const [ops, setOps] = useState<ReadonlySet<StressOp>>(new Set(STRESS_OPS));
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  function toggle(op: StressOp): void {
    setOps((current) => {
      const next = new Set(current);
      if (next.has(op)) next.delete(op);
      else next.add(op);
      return next;
    });
  }

  async function run(): Promise<void> {
    setPhase({ kind: 'running' });
    try {
      const original = await client.getJob(jobId);
      const created = await client.stress(
        jobId,
        STRESS_OPS.filter((op) => ops.has(op)),
      );
      const deadline = Date.now() + timeoutMs;
      let derived = await client.getJob(created.job_id);
      while (!isSettled(derived)) {
        if (!alive.current) return;
        if (Date.now() >= deadline) throw new Error('stress-timeout');
        await delay(pollMs);
        derived = await client.getJob(created.job_id);
      }
      if (!alive.current) return;
      setPhase({
        kind: 'done',
        comparison: {
          derivedJobId: created.job_id,
          before: summarizeJob(original),
          after: summarizeJob(derived),
        },
      });
    } catch (error) {
      if (alive.current) setPhase({ kind: 'error', message: errorMessage(error) });
    }
  }

  const running = phase.kind === 'running';
  const m = dashboard.stressMetrics;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-4">
        <Button
          variant="secondary"
          iconStart={<Zap className="size-4" />}
          loading={running}
          onClick={() => setPhase({ kind: 'confirming' })}
        >
          {dashboard.stressRun}
        </Button>
        <p className="text-sm text-ink-muted">{dashboard.stressHint}</p>
      </div>

      <Dialog
        open={phase.kind === 'confirming'}
        onOpenChange={(open) => {
          if (!open) setPhase({ kind: 'idle' });
        }}
        title={dashboard.stressConfirmTitle}
        description={dashboard.stressConfirmBody}
      >
        <fieldset className="mb-4 flex flex-col gap-2">
          <legend className="mb-2 text-sm font-semibold text-ink-strong">
            {dashboard.stressOpsLabel}
          </legend>
          {STRESS_OPS.map((op) => (
            <label key={op} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={ops.has(op)} onChange={() => toggle(op)} />
              {dashboard.stressOps[op]}
            </label>
          ))}
          {ops.size === 0 ? (
            <p role="alert" className="text-sm text-state-review-fg">
              {dashboard.stressNoOps}
            </p>
          ) : null}
        </fieldset>
        <div className="flex gap-3">
          <Button disabled={ops.size === 0} onClick={() => void run()}>
            {dashboard.stressConfirm}
          </Button>
          <Button variant="ghost" onClick={() => setPhase({ kind: 'idle' })}>
            {dashboard.stressCancel}
          </Button>
        </div>
      </Dialog>

      <div aria-live="polite">
        {running ? <p className="text-sm text-ink-muted">{dashboard.stressRunning}</p> : null}
        {phase.kind === 'error' ? (
          <p role="alert" className="text-sm text-state-review-fg">
            {phase.message}
          </p>
        ) : null}
      </div>

      {phase.kind === 'done' ? (
        <section aria-label={dashboard.stressResultTitle} className="flex flex-col gap-3">
          <h3 className="text-lg font-semibold text-ink-strong">{dashboard.stressResultTitle}</h3>
          <Table caption={dashboard.stressResultCaption}>
            <THead>
              <Tr>
                <Th>{dashboard.stressColumns.metric}</Th>
                <Th>{dashboard.stressColumns.before}</Th>
                <Th>{dashboard.stressColumns.after}</Th>
              </Tr>
            </THead>
            <TBody>
              <Tr data-metric="rows">
                <Th scope="row">{m.rows}</Th>
                <Td className="px-3 py-2">{ltr(String(phase.comparison.before.rows))}</Td>
                <Td className="px-3 py-2">{ltr(String(phase.comparison.after.rows))}</Td>
              </Tr>
              <Tr data-metric="flagged">
                <Th scope="row">{m.flagged}</Th>
                <Td className="px-3 py-2">{ltr(String(phase.comparison.before.flagged))}</Td>
                <Td className="px-3 py-2">{ltr(String(phase.comparison.after.flagged))}</Td>
              </Tr>
              <Tr data-metric="local">
                <Th scope="row">{m.local}</Th>
                <Td className="px-3 py-2">{ltr(pct(phase.comparison.before.localCloseRate))}</Td>
                <Td className="px-3 py-2">{ltr(pct(phase.comparison.after.localCloseRate))}</Td>
              </Tr>
            </TBody>
          </Table>
          <p className="text-sm text-ink-muted">{dashboard.stressExpectation}</p>
          <Link
            className="text-sm font-medium text-primary-700 underline"
            to={`/jobs/${encodeURIComponent(phase.comparison.derivedJobId)}/review`}
          >
            {dashboard.stressOpenReview}
          </Link>
        </section>
      ) : null}
    </div>
  );
}
