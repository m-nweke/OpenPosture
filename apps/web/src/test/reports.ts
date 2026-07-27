/**
 * Report fixtures, built from the real serialiser's shape.
 *
 * Copied from `PostureReport.to_dict()` output rather than invented, so a test asserting on
 * `quality.gaps` is asserting on the field the API actually sends. When OP-45 generates the types
 * from the OpenAPI schema, a drift between these and the backend becomes a compile error.
 */

import type { AnalysisResponse, Metric, PostureReport } from '../api/types'

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

/** A frontal photo: measured, but the view precondition is not met. */
export function frontalViewReport(): PostureReport {
  const report = hunchbackReport()
  report.metrics.view_confidence = ok(0.72, 'ratio', 'photographed from the front')
  return report
}

export function analysisOf(report: PostureReport | null): AnalysisResponse {
  return {
    object_key: 'analyses/0123456789abcdef0123456789abcdef.jpg',
    pose_detected: report !== null,
    report,
    image: { width: 640, height: 480 },
  }
}
