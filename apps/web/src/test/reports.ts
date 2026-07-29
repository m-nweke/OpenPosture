/**
 * Report fixtures, built from the real serialiser's shape.
 *
 * Copied from `PostureReport.to_dict()` output rather than invented, so a test asserting on
 * `quality.gaps` is asserting on the field the API actually sends. When OP-45 generates the types
 * from the OpenAPI schema, a drift between these and the backend becomes a compile error.
 */

import type { AnalysisResponse, Metric, PostureReport } from '../api/types'

type Landmark = AnalysisResponse['landmarks'][number]

function ok(value: number, unit: string, detail: string): Metric {
  return { value, unit, status: 'ok', detail, confidence: 0.95 }
}

function unavailable(unit: string, detail: string): Metric {
  return { value: null, unit, status: 'low_confidence', detail, confidence: null }
}

/** A lateral photo of someone slouching: two findings, everything measured. */
export function hunchbackReport(overrides: Partial<PostureReport> = {}): PostureReport {
  return {
    schema_version: '1.0',
    rules_version: '1.0.0',
    backend: 'fake',
    inference_ms: 0,
    image: { width: 640, height: 480 },
    overall_score: 70,
    findings: [
      {
        code: 'trunk_slouch',
        severity: 'major',
        message: 'Your torso is leaning 32° forward. Try bringing your hips back into the chair.',
        metric: 'trunk_inclination_deg',
        value: 32,
        confidence: 0.95,
      },
      {
        code: 'forward_head',
        severity: 'major',
        message: 'Your head sits well forward of your shoulders (craniovertebral angle 28°).',
        metric: 'craniovertebral_angle_deg',
        value: 28,
        confidence: 0.95,
      },
    ],
    metrics: {
      view_confidence: ok(0.0, 'ratio', 'photographed from the side'),
      trunk_inclination_deg: ok(32, 'deg', 'leaning 32° forward — a pronounced slouch'),
      craniovertebral_angle_deg: ok(28, 'deg', 'head is well forward of your shoulders'),
      knee_flexion_deg: ok(100, 'deg', 'your left knee is at a comfortable seated angle'),
    },
    quality: {
      assessed: 4,
      total: 4,
      coverage: 1,
      gaps: [],
      keypoints: {},
    },
    ...overrides,
  }
}

/** Nothing could be measured: every metric a gap, no score. */
export function allGapsReport(): PostureReport {
  return hunchbackReport({
    overall_score: null,
    findings: [],
    metrics: {
      trunk_inclination_deg: unavailable('deg', 'left hip and left shoulder were unclear'),
      knee_flexion_deg: unavailable('deg', 'left knee and left ankle were out of frame'),
    },
    quality: {
      assessed: 0,
      total: 2,
      coverage: 0,
      gaps: [
        {
          metric: 'trunk_inclination_deg',
          status: 'low_confidence',
          detail: 'left hip and left shoulder were unclear',
          keypoints: { left_hip: 'low_confidence', left_shoulder: 'low_confidence' },
        },
        {
          metric: 'knee_flexion_deg',
          status: 'insufficient_keypoints',
          detail: 'left knee and left ankle were out of frame',
          keypoints: { left_knee: 'out_of_frame', left_ankle: 'out_of_frame' },
        },
      ],
      keypoints: {},
    },
  })
}

/**
 * A frontal photo: measured, but the view precondition is not met.
 *
 * Carries the engine's own `frontal_view` finding, message included verbatim from
 * `pose_backends.cli --preset frontal_view`. The UI branches on that code rather than on the
 * ratio, so a fixture that only moved the ratio would exercise nothing.
 */
export function frontalViewReport(): PostureReport {
  const report = hunchbackReport()
  report.metrics.view_confidence = ok(0.8776, 'ratio', 'photographed from the front')
  report.findings = [
    ...report.findings,
    {
      code: 'frontal_view',
      severity: 'info',
      message:
        'This photo looks like it was taken from the front. The measurements below still work, ' +
        "but they rest on a depth estimate that is weakest along the camera's own axis. A " +
        'side-on photo would give a firmer answer.',
      metric: 'view_confidence',
      value: 0.8776,
      confidence: 0.95,
    },
  ]
  return report
}

function point(name: string, x: number, y: number, status: Landmark['status'] = 'ok'): Landmark {
  return {
    name,
    x,
    y,
    status,
    visibility: status === 'ok' ? 0.95 : 0.2,
    presence: status === 'out_of_frame' ? 0.1 : 0.98,
  }
}

/**
 * A skeleton with a deliberate mix of statuses.
 *
 * Six `ok`, one `low_confidence`, one `not_detected` — so a test can assert exactly how many
 * points are drawn and how many are drawn *differently*, which is the whole contract of the
 * overlay. A fixture where everything is `ok` would pass against a component that ignored status.
 */
export function landmarksWithGaps(): Landmark[] {
  return [
    point('left_shoulder', 0.4, 0.3),
    point('right_shoulder', 0.5, 0.3),
    point('left_hip', 0.4, 0.6),
    point('right_hip', 0.5, 0.6),
    point('left_knee', 0.42, 0.8),
    point('left_ankle', 0.44, 0.95),
    point('left_elbow', 0.35, 0.45, 'low_confidence'),
    point('left_wrist', 0.3, 0.55, 'not_detected'),
  ]
}

export function analysisOf(
  report: PostureReport | null,
  landmarks: Landmark[] = report ? landmarksWithGaps() : [],
): AnalysisResponse {
  return {
    // Fixed rather than random: a fixture that changes between runs cannot be asserted against,
    // and nothing in the UI derives meaning from the value beyond passing it back to the API.
    id: '00000000-0000-4000-8000-000000000001',
    object_key: 'analyses/0123456789abcdef0123456789abcdef.jpg',
    pose_detected: report !== null,
    report,
    landmarks,
    image: { width: 640, height: 480 },
  }
}
