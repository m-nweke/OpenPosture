/**
 * The API's types, aliased from the generated schema.
 *
 * **Nothing here is hand-written any more.** Every type below points into `schema.d.ts`, which
 * `npm run codegen` produces from the backend's OpenAPI document. Rename a field in Python and
 * `tsc` fails here, rather than a browser producing `undefined` some months later.
 *
 * This file exists only to give those generated names readable, stable aliases —
 * `components['schemas']['PostureReportModel']` is accurate and unpleasant to read. Adding a
 * *shape* here rather than an alias would reintroduce the second source of truth this ticket
 * removed, so don't.
 */

import type { components } from './schema'

type Schemas = components['schemas']

/** Why a metric has no value. Generated from `posture_core.MetricStatus`. */
export type MetricStatus = Schemas['Metric']['status']

export type KeypointStatus = Schemas['Gap']['keypoints'][string]

export type Severity = Schemas['Finding']['severity']

export type Metric = Schemas['Metric']
export type Finding = Schemas['Finding']
export type Gap = Schemas['Gap']
export type Quality = Schemas['Quality']

/** The report itself. Named `PostureReportModel` on the wire; the alias drops the suffix. */
export type PostureReport = Schemas['PostureReportModel']

export type AnalysisResponse = Schemas['AnalysisResponse']

/** One row in the history list. */
export type AnalysisListItem = Schemas['AnalysisListItem']

/** One page of `GET /analyses`. */
export type AnalysisPage = Schemas['AnalysisPage']

/** One point on the trunk-inclination trend line. `value` is `null` on a gap, never `0`. */
export type TrendPoint = Schemas['TrendPoint']

/** The full trend returned by `GET /analyses/metrics/trunk-inclination`. */
export type TrendSeries = Schemas['TrendSeries']

/** The body of `POST /auth/register` and `POST /auth/login`. */
export type CredentialsRequest = Schemas['CredentialsRequest']

/** What every successful auth route returns. The refresh token is never in here — see the type. */
export type TokenResponse = Schemas['TokenResponse']

/**
 * An RFC 9457 problem document.
 *
 * Still hand-written, and deliberately: FastAPI documents error *statuses* but not the shape of
 * this body, because the handlers build it directly rather than through a response model. Worth
 * a follow-up — until then this is the one type here that can drift, and the frontend branches
 * only on `type`, which is the field least likely to.
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
