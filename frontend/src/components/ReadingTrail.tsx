import { Cloud, Monitor } from 'lucide-react';

import type { ReadingTrailStep } from '@/api/types';
import { review } from '@/copy/he';
import { ltr } from '@/lib/rtl';

export interface ReadingTrailProps {
  trail: readonly ReadingTrailStep[];
  /** Isolate values as digits/dates (LTR). */
  isolateValues?: boolean;
}

/**
 * Who read what, in cascade order. A step that left the machine is told apart by icon, wording
 * and colour together: local green with a monitor, cloud magenta with a cloud and the note that
 * only a crop was sent.
 */
export function ReadingTrail({ trail, isolateValues = false }: ReadingTrailProps) {
  if (trail.length === 0) {
    return <p className="text-sm text-ink-muted">{review.trailEmpty}</p>;
  }

  return (
    <ol aria-label={review.trailTitle} className="flex flex-wrap gap-2">
      {trail.map((step, index) => {
        const Icon = step.cloud ? Cloud : Monitor;
        return (
          <li
            key={`${step.stage}-${step.reader}-${index}`}
            data-cloud={step.cloud ? 'true' : 'false'}
            data-reader={step.reader}
            className={`flex flex-wrap items-center gap-2 rounded-md border bg-surface-raised px-2 py-1 text-sm ${
              step.cloud ? 'border-cloud text-cloud' : 'border-local text-local'
            }`}
          >
            <Icon aria-hidden="true" className="size-4 shrink-0" />
            <span className="font-medium">{review.readers[step.reader]}</span>
            {step.value !== null ? (
              <span className="text-ink-strong">{isolateValues ? ltr(step.value) : step.value}</span>
            ) : null}
            <span className="text-xs">{review.trailOutcome[step.outcome]}</span>
            {step.cloud ? <span className="text-xs font-medium">{review.cloudChip}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
