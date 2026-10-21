import type { ConfidenceLevel } from '@/api/types';
import { review } from '@/copy/he';
import { LEVEL_STYLE, LEVELS } from '@/lib/level';

export interface ConfidenceRibbonProps {
  level: ConfidenceLevel;
  flaggedCount: number;
  /** Batch totals per level. When set, renders proportional segments instead of one bar. */
  aggregate?: Partial<Record<ConfidenceLevel, number>>;
  onSelectLevel?: (level: ConfidenceLevel) => void;
}

/** Row ribbon: a 4px bar on the inline-start edge (the right edge in RTL) with a tick per flag. */
export function ConfidenceRibbon({
  level,
  flaggedCount,
  aggregate,
  onSelectLevel,
}: ConfidenceRibbonProps) {
  if (aggregate) {
    const total = LEVELS.reduce((sum, l) => sum + (aggregate[l] ?? 0), 0);
    return (
      <div className="flex h-2 w-full overflow-hidden rounded-pill" role="group">
        {LEVELS.map((l) => {
          const count = aggregate[l] ?? 0;
          if (count === 0 || total === 0) return null;
          const label = review.levels[l];
          const style = { width: `${(count / total) * 100}%` };
          return onSelectLevel ? (
            <button
              key={l}
              type="button"
              aria-label={label}
              title={label}
              style={style}
              className={LEVEL_STYLE[l].bar}
              onClick={() => onSelectLevel(l)}
            />
          ) : (
            <span key={l} role="img" aria-label={label} style={style} className={LEVEL_STYLE[l].bar} />
          );
        })}
      </div>
    );
  }

  return (
    <span
      data-ribbon={level}
      className={`absolute inset-y-0 start-0 flex w-1 flex-col justify-start gap-1 ${LEVEL_STYLE[level].bar}`}
    >
      <span className="sr-only">
        {review.levels[level]} · {review.rowRibbon(flaggedCount)}
      </span>
      {Array.from({ length: flaggedCount }, (_, i) => (
        <span
          key={i}
          aria-hidden="true"
          className="mt-1 h-0.5 w-3 bg-state-review-border"
          data-tick=""
        />
      ))}
    </span>
  );
}
