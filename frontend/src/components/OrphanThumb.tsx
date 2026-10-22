import type { Box } from '@/api/types';
import { review } from '@/copy/he';
import { ltr } from '@/lib/rtl';
import { useCrop } from '@/lib/useCrop';
import type { CropClient } from '@/lib/useCrop';

/** The whole page, as normalised coordinates: a thumbnail is a crop of everything. */
const WHOLE_PAGE: Box = [0, 0, 1, 1];
/** No padding around a whole-page crop. */
const NO_PAD = 0;

export interface OrphanThumbProps {
  jobId: string;
  pageId: string;
  client?: CropClient;
}

/** One unattached page with a thumbnail and the reason it is here. A missing image is not an error. */
export function OrphanThumb({ jobId, pageId, client }: OrphanThumbProps) {
  const crop = useCrop(jobId, pageId, WHOLE_PAGE, NO_PAD, client);
  return (
    <li
      data-orphan={pageId}
      className="flex items-center gap-3 rounded-md border border-surface-border bg-surface-raised p-3"
    >
      <div className="flex h-20 w-14 shrink-0 items-center justify-center overflow-hidden rounded-sm bg-surface-sunken">
        {crop.url ? (
          <img src={crop.url} alt={review.orphanThumb(pageId)} className="h-full w-full object-contain" />
        ) : (
          <span className="px-1 text-center text-xs text-ink-muted">
            {crop.isError ? review.lensMissing : review.lensLoading}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <span>{ltr(pageId)}</span>
        <span className="text-sm text-ink-base">{review.orphanReason}</span>
      </div>
    </li>
  );
}
