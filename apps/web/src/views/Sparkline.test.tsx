import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import Sparkline from './Sparkline'
import type { TrendPoint } from '../api/types'

/** A point as the API returns it, newest first, with sensible defaults for what a test ignores. */
function point(overrides: Partial<TrendPoint>): TrendPoint {
  return {
    created_at: '2026-01-01T00:00:00Z',
    value: 12.0,
    status: 'ok',
    rules_version: '1.0.0',
    ...overrides,
  }
}

describe('Sparkline', () => {
  it('shows an empty state instead of a broken chart for no history', () => {
    render(<Sparkline points={[]} />)

    expect(screen.getByText(/No trend yet/)).toBeInTheDocument()
    expect(screen.queryByTestId('sparkline')).not.toBeInTheDocument()
  })

  it('renders a single point on its own, with no visible line', () => {
    render(<Sparkline points={[point({ value: 15 })]} />)

    expect(screen.getAllByTestId('trend-point')).toHaveLength(1)
    // A polyline needs two coordinates to draw anything; one point alone leaves nothing visible,
    // even though a degenerate single-coordinate `<polyline>` may still be present in the DOM.
    for (const segment of screen.queryAllByTestId('trend-segment')) {
      expect(segment.getAttribute('points')).not.toContain(' ')
    }
  })

  it('breaks the line and marks a gap distinctly, never as a zero', () => {
    // Newest first, as the API returns them. Reversed for rendering, the gap sits between one
    // isolated point and a real two-point run — proving the line does not reach across it.
    const points: TrendPoint[] = [
      point({ created_at: '2026-01-04T00:00:00Z', value: 20 }),
      point({ created_at: '2026-01-03T00:00:00Z', value: 18 }),
      point({
        created_at: '2026-01-02T00:00:00Z',
        value: null,
        status: 'insufficient_keypoints',
      }),
      point({ created_at: '2026-01-01T00:00:00Z', value: 10 }),
    ]

    render(<Sparkline points={points} />)

    expect(screen.getAllByTestId('trend-point')).toHaveLength(3)
    expect(screen.getAllByTestId('trend-gap')).toHaveLength(1)
    // Two separate pieces, not one line spanning all three real points — the isolated point
    // before the gap draws nothing, and the pair after it draws one real segment.
    const segments = screen.getAllByTestId('trend-segment')
    expect(segments).toHaveLength(2)
    const withAVisibleLine = segments.filter((segment) =>
      segment.getAttribute('points')?.includes(' '),
    )
    expect(withAVisibleLine).toHaveLength(1)
  })

  it('marks a rules_version boundary within the series', () => {
    const points: TrendPoint[] = [
      point({ created_at: '2026-01-02T00:00:00Z', value: 18, rules_version: '2.0.0' }),
      point({ created_at: '2026-01-01T00:00:00Z', value: 10, rules_version: '1.0.0' }),
    ]

    render(<Sparkline points={points} />)

    expect(screen.getByTestId('trend-version-boundary')).toBeInTheDocument()
  })

  it('draws no boundary when the ruleset never changes', () => {
    const points: TrendPoint[] = [
      point({ created_at: '2026-01-02T00:00:00Z', value: 18, rules_version: '1.0.0' }),
      point({ created_at: '2026-01-01T00:00:00Z', value: 10, rules_version: '1.0.0' }),
    ]

    render(<Sparkline points={points} />)

    expect(screen.queryByTestId('trend-version-boundary')).not.toBeInTheDocument()
  })
})
