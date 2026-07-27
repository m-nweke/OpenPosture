/**
 * The shape of what the API returns.
 *
 * **Hand-written, and temporarily so.** OP-45 generates these from the backend's OpenAPI schema
 * with `openapi-typescript` and deletes this file. Until then they are a second source of truth,
 * which is exactly the thing that rots: rename a field in Python and nothing here complains until
 * a `undefined` shows up in the browser months later.
 *
 * Kept deliberately close to `PostureReport.to_dict()` so the swap is a re-import, not a rewrite.
 */

/** Why a metric has no value. Mirrors `posture_core.MetricStatus`. */
export type MetricStatus = 'ok' | 'insufficient_keypoints' | 'low_confidence'

/** Mirrors `posture_core.KeypointStatus`. */
export type KeypointStatus = 'ok' | 'low_confidence' | 'not_detected' | 'out_of_frame'

export type Severity = 'major' | 'minor' | 'info'

export interface Metric {
  /** `null` whenever `status !== 'ok'`. Never a guess — that distinction is the whole point. */
  value: number | null
  unit: string
  status: MetricStatus
  /** Human-readable, already phrased for display by the engine. */
  detail: string
  confidence: number | null
}

export interface Finding {
  code: string
  severity: Severity
  message: string
  metric: string
  value: number
  confidence: number
}

/** One metric the engine could not assess, and what stopped it. */
export interface Gap {
  metric: string
  status: MetricStatus
  detail: string
  keypoints: Record<string, KeypointStatus>
}

export interface Quality {
  assessed: number
  total: number
  coverage: number
  gaps: Gap[]
  keypoints: Record<string, KeypointStatus>
}

export interface PostureReport {
  schema_version: string
  rules_version: string
  backend: string
  inference_ms: number
  image: { width: number; height: number }
  overall_score: number | null
  findings: Finding[]
  metrics: Record<string, Metric>
  quality: Quality
}

export interface AnalysisResponse {
  object_key: string
  pose_detected: boolean
  /** `null` exactly when `pose_detected` is false. */
  report: PostureReport | null
  image: { width: number; height: number }
}

/**
 * An RFC 9457 problem document.
 *
 * `type` is the field worth branching on — it is stable and machine-readable, where `title` and
 * `detail` are prose a copy edit can change.
 */
export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  instance?: string
  request_id?: string
  [key: string]: unknown
}
