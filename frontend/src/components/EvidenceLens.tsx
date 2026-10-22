import { useState } from 'react';

import type { Box } from '@/api/types';
import { review } from '@/copy/he';
import { useCrop } from '@/lib/useCrop';
import type { CropClient } from '@/lib/useCrop';

/** Magnification of the crop. A structural default from the plan (×3), not a tuned threshold. */
const DEFAULT_ZOOM = 3;

export interface EvidenceLensProps {
  jobId: string;
  pageId: string;
  box: Box | null;
  /** Default 3. */
  zoom?: number;
  /** Accessible description of what is shown, e.g. the field name. */
  label?: string;
  /** Injected in tests; the app uses the configured client. */
  client?: CropClient;
}

function percent(value: number): string {
  return `${value * 100}%`;
}

/**
 * Shows where a field was read: a page locator whose frame glides to the field's box (the motion
 * is what makes the spatial relationship legible), and the crop magnified beside it.
 * A missing image or box renders a placeholder, never an error.
 */
export function EvidenceLens({ jobId, pageId, box, zoom = DEFAULT_ZOOM, label = '', client }: EvidenceLensProps) {
  const crop = useCrop(jobId, pageId, box, undefined, client);
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);

  if (box === null) {
    return (
      <p data-lens="no-box" className="text-sm text-ink-muted">
        {review.lensNoBox}
      </p>
    );
  }

  const [top, left, bottom, right] = box;

  return (
    <figure data-lens="ready" data-zoom={zoom} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        {/* The page is not mirrored in RTL, so the locator is explicitly left to right. */}
        <div
          dir="ltr"
          role="img"
          aria-label={review.pageLocator}
          className="relative aspect-[1/1.414] w-20 shrink-0 rounded-sm border border-surface-border bg-surface-sunken"
        >
          <div
            data-testid="lens-frame"
            data-box={box.join(',')}
            className="absolute rounded-sm border-2 border-accent-500 bg-accent-100/60 transition-[top,left,width,height] duration-base ease-token motion-reduce:transition-none"
            style={{
              top: percent(top),
              left: percent(left),
              height: percent(bottom - top),
              width: percent(right - left),
            }}
          />
        </div>

        <div className="min-w-0 flex-1">
          {crop.isError ? (
            <p data-lens="missing" role="status" className="text-sm text-ink-muted">
              {review.lensMissing}
            </p>
          ) : crop.url === null ? (
            <p data-lens="loading" role="status" className="text-sm text-ink-muted">
              {review.lensLoading}
            </p>
          ) : (
            <div tabIndex={0} role="group" aria-label={review.lensRegion} className="max-h-48 overflow-auto rounded-md border border-surface-border bg-surface-raised">
              <img
                src={crop.url}
                alt={review.lensAlt(label)}
                onLoad={(event) =>
                  setNatural({
                    width: event.currentTarget.naturalWidth,
                    height: event.currentTarget.naturalHeight,
                  })
                }
                style={
                  natural && natural.width > 0
                    ? { width: natural.width * zoom, height: natural.height * zoom, maxWidth: 'none' }
                    : undefined
                }
              />
            </div>
          )}
        </div>
      </div>
    </figure>
  );
}
