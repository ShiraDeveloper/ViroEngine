import type { RunMetric } from '@/api/types';
import { dashboard } from '@/copy/he';
import { EmptyState } from '@/design/EmptyState';
import { formatRate } from '@/lib/metrics';

/** Structural: drawing area of the sparkline, in SVG user units. */
const WIDTH = 320;
const HEIGHT = 96;
const PAD = 8;

export interface LearningChartProps {
  points: readonly RunMetric[];
}

/** Local close rate across runs. Fewer than two points is an explanation, not a one-point line. */
export function LearningChart({ points }: LearningChartProps) {
  const usable = points.filter((p) => p.fields_total > 0);
  if (usable.length < 2) {
    return <EmptyState title={dashboard.learningEmpty} />;
  }
  const rates = usable.map((p) => p.local_close_rate);
  const lo = Math.min(...rates);
  const hi = Math.max(...rates);
  const span = hi - lo || 1;
  const step = (WIDTH - 2 * PAD) / (usable.length - 1);
  // Runs read right to left: the first run sits at the inline-start (right) edge.
  const coords = usable.map((p, i) => {
    const x = WIDTH - PAD - i * step;
    const y = HEIGHT - PAD - ((p.local_close_rate - lo) / span) * (HEIGHT - 2 * PAD);
    return { x, y };
  });
  const first = formatRate(rates[0], 0);
  const last = formatRate(rates[rates.length - 1], 0);
  const label = first === last ? dashboard.learningFlat(last) : dashboard.learningLabel(first, last);
  return (
    <figure className="rounded-lg border border-surface-border bg-surface-raised p-4 shadow-card">
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-24 w-full max-w-md text-primary-700"
      >
        <polyline
          points={coords.map((c) => `${c.x},${c.y}`).join(' ')}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
        {coords.map((c, i) => (
          <circle key={usable[i]?.at ?? i} cx={c.x} cy={c.y} r="3" fill="currentColor" />
        ))}
      </svg>
      <figcaption className="mt-2 text-sm text-ink-muted">{dashboard.learningAxis}</figcaption>
    </figure>
  );
}
