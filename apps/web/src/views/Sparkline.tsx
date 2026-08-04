/**
 * The trunk-inclination trend line, from a plain SVG — no charting library.
 *
 * A sparkline over a handful of points does not need one, and this project already has a rule
 * against reaching for a dependency a hand-rolled fifty lines covers just as well.
 *
 * Two correctness rules from the ticket, both non-negotiable:
 *
 * **Gaps are not zeros.** An analysis with no measured value breaks the line rather than
 * interpolating through it, and gets its own hollow marker on the baseline — plotting it as `0`
 * would invent an upright posture the user never had, the original engine's central defect in
 * chart form.
 *
 * **A `rules_version` change is marked, not hidden.** A dashed vertical guide appears at the
 * boundary, because a step in the line at that point is a retuned threshold, not a change in the
 * user's posture, and the two must not look the same.
 */

import type { TrendPoint } from '../api/types'
import styles from './Sparkline.module.css'

interface Props {
  /** Newest first, as `GET /analyses/metrics/trunk-inclination` returns them. */
  points: TrendPoint[]
}

const WIDTH = 320
const HEIGHT = 96
const PADDING_Y = 12

export default function Sparkline({ points }: Props) {
  if (points.length === 0) {
    return <p className={styles.empty}>No trend yet — analyse a few more photos to see one.</p>
  }

  // The API orders newest first; a trend line reads left-to-right in time.
  const ordered = [...points].reverse()

  const values = ordered.map((p) => p.value).filter((v): v is number => v !== null)
  const min = values.length > 0 ? Math.min(...values) : 0
  const max = values.length > 0 ? Math.max(...values) : 0
  // A flat or single-value series would divide by zero below; give it a nominal span instead.
  const range = max - min || 1

  const xStep = ordered.length > 1 ? WIDTH / (ordered.length - 1) : 0
  const xFor = (i: number) => (ordered.length > 1 ? i * xStep : WIDTH / 2)
  const yFor = (value: number) =>
    HEIGHT - PADDING_Y - ((value - min) / range) * (HEIGHT - PADDING_Y * 2)

  // Consecutive real values form one polyline; a gap ends the current one rather than being
  // interpolated through, so the line itself never implies a value nobody measured.
  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  for (const [i, point] of ordered.entries()) {
    if (point.value === null) {
      if (current.length > 0) segments.push(current)
      current = []
      continue
    }
    current.push({ x: xFor(i), y: yFor(point.value) })
  }
  if (current.length > 0) segments.push(current)

  const versionBoundaries = ordered
    .map((point, i) => ({ point, i }))
    .filter(({ point, i }) => i > 0 && ordered[i - 1]?.rules_version !== point.rules_version)

  return (
    <svg
      className={styles.sparkline}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-label="Trunk inclination over time"
      preserveAspectRatio="none"
      data-testid="sparkline"
    >
      {versionBoundaries.map(({ point, i }) => (
        <line
          key={`boundary-${point.created_at}-${i}`}
          data-testid="trend-version-boundary"
          className={styles.versionBoundary}
          x1={xFor(i)}
          x2={xFor(i)}
          y1={0}
          y2={HEIGHT}
        >
          <title>{`Rules changed to ${point.rules_version} here.`}</title>
        </line>
      ))}

      {segments.map((segment, i) => (
        <polyline
          key={`segment-${i}`}
          data-testid="trend-segment"
          className={styles.line}
          points={segment.map((p) => `${p.x},${p.y}`).join(' ')}
        />
      ))}

      {ordered.map((point, i) =>
        point.value === null ? (
          <circle
            key={`gap-${point.created_at}-${i}`}
            data-testid="trend-gap"
            className={styles.gapMarker}
            cx={xFor(i)}
            cy={HEIGHT - PADDING_Y}
            r={3}
          >
            <title>{`No trunk inclination measured (${point.status}).`}</title>
          </circle>
        ) : (
          <circle
            key={`point-${point.created_at}-${i}`}
            data-testid="trend-point"
            className={styles.point}
            cx={xFor(i)}
            cy={yFor(point.value)}
            r={ordered.length === 1 ? 4 : 2.5}
          >
            <title>{`${Math.round(point.value)}°`}</title>
          </circle>
        ),
      )}
    </svg>
  )
}
