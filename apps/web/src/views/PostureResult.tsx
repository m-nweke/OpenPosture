/**
 * Rendering one analysis.
 *
 * Split out of `Dashboard` because the dashboard's job is the *flow* — pick a file, upload it,
 * handle what comes back — and this one's job is presenting a report. They change for different
 * reasons, and the canvas overlay in OP-46 attaches here rather than to the upload logic.
 *
 * Two presentation rules carry the project's argument:
 *
 * **Gaps are shown, not hidden.** A metric the engine could not assess appears with its reason,
 * because "couldn't assess your knees, try a wider shot" is information. The original silently
 * reported "Straight back position" whenever assessment failed, which is the same act of
 * concealment with a worse outcome.
 *
 * **A partially reliable result is labelled.** Low view confidence gets a visible caveat rather
 * than being presented as fact.
 */

import type { PostureReport } from '../api/types'
import styles from './PostureResult.module.css'

/** Below this, the shoulder-width ratio says the photo is probably not lateral. */
const LATERAL_VIEW_THRESHOLD = 0.35

interface Props {
  report: PostureReport
  imageUrl: string | null
}

export default function PostureResult({ report, imageUrl }: Props) {
  const measured = Object.entries(report.metrics).filter(([, metric]) => metric.status === 'ok')
  const viewConfidence = report.metrics.view_confidence
  const viewIsDoubtful =
    viewConfidence?.status === 'ok' &&
    viewConfidence.value !== null &&
    viewConfidence.value > LATERAL_VIEW_THRESHOLD

  return (
    <section className={styles.results} aria-labelledby="results-heading">
      <h2 id="results-heading">Your results</h2>

      {viewIsDoubtful && (
        // `role="status"` rather than `alert`: it is a caveat on a result the user asked for,
        // not an interruption. Screen readers announce it without stealing focus.
        <p className={styles.caveat} role="status">
          This photo does not look like it was taken from the side, so these measurements are less
          reliable than usual. A lateral photo gives a much better reading.
        </p>
      )}

      {report.overall_score !== null ? (
        <p className={styles.score}>
          <span className={styles.scoreValue}>{Math.round(report.overall_score)}</span>
          <span className={styles.scoreOutOf}>/ 100</span>
        </p>
      ) : (
        // Not a zero and not a hundred. Both would be confident claims about a photograph the
        // engine could not assess.
        <p className={styles.score}>Not enough was visible to score this photo.</p>
      )}

      <p className={styles.coverage}>
        Assessed {report.quality.assessed} of {report.quality.total} measurements.
      </p>

      {imageUrl && (
        <img src={imageUrl} alt="The photo you uploaded" className={styles.uploadedImage} />
      )}

      {report.findings.length > 0 ? (
        <section aria-labelledby="findings-heading">
          <h3 id="findings-heading">What we noticed</h3>
          <ul className={styles.findings}>
            {report.findings.map((finding) => (
              <li key={finding.code} className={styles[finding.severity] ?? ''}>
                <span className={styles.severityTag}>{finding.severity}</span>
                {finding.message}
              </li>
            ))}
          </ul>
        </section>
      ) : (
        report.quality.assessed > 0 && (
          <p className={styles.noFindings}>
            Nothing stood out in what we could measure. That is a good sign.
          </p>
        )
      )}

      {measured.length > 0 && (
        <section aria-labelledby="measurements-heading">
          <h3 id="measurements-heading">Measurements</h3>
          <dl className={styles.metrics}>
            {measured.map(([name, metric]) => (
              <div key={name} className={styles.metricRow}>
                <dt>{humanise(name)}</dt>
                <dd>
                  <strong>{formatValue(metric.value, metric.unit)}</strong>
                  <span className={styles.metricDetail}>{metric.detail}</span>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {report.quality.gaps.length > 0 && (
        <section aria-labelledby="gaps-heading">
          <h3 id="gaps-heading">What we could not assess</h3>
          <ul className={styles.gaps}>
            {report.quality.gaps.map((gap) => (
              <li key={gap.metric}>
                <strong>{humanise(gap.metric)}</strong> — {gap.detail}
              </li>
            ))}
          </ul>
          <p className={styles.gapAdvice}>
            A photo taken from further back, with your whole body in frame, usually fixes this.
          </p>
        </section>
      )}
    </section>
  )
}

/** `trunk_inclination_deg` → `Trunk inclination`. The unit is rendered beside the value. */
function humanise(metricName: string): string {
  const words = metricName
    .replace(/_deg$|_m$/, '')
    .split('_')
    .join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function formatValue(value: number | null, unit: string): string {
  if (value === null) return '—'
  const rounded = Math.round(value * 100) / 100
  if (unit === 'deg') return `${rounded}°`
  if (unit === 'm') return `${rounded} m`
  return `${rounded}`
}
