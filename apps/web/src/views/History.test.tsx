import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import History from './History'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/mswServer'
import { mockSignedIn } from '../test/authFixtures'
import { ANALYSES_ENDPOINT } from '../api/client'
import type { AnalysisListItem, AnalysisPage, TrendSeries } from '../api/types'

const TREND_ENDPOINT = `${ANALYSES_ENDPOINT}/metrics/trunk-inclination`

function signedInAs() {
  mockSignedIn({ id: 'u1', email: 'ada@example.com', displayName: 'Ada' })
}

function item(overrides: Partial<AnalysisListItem> = {}): AnalysisListItem {
  return {
    id: 'a1',
    created_at: '2026-01-01T00:00:00Z',
    object_key: 'analyses/a1.jpg',
    image_url: '/media/analyses/a1.jpg',
    pose_detected: true,
    overall_score: 72,
    ...overrides,
  }
}

function respondWithPage(page: AnalysisPage) {
  server.use(http.get(ANALYSES_ENDPOINT, () => HttpResponse.json(page)))
}

function respondWithTrend(series: TrendSeries) {
  server.use(http.get(TREND_ENDPOINT, () => HttpResponse.json(series)))
}

/** Both requests `History` fires on mount, answered with the emptiest valid response. */
function respondEmpty() {
  respondWithPage({ items: [], next_cursor: null })
  respondWithTrend({ points: [] })
}

describe('History', () => {
  it('says so plainly when there is nothing yet, rather than an empty list with no explanation', async () => {
    signedInAs()
    respondEmpty()
    renderWithProviders(<History />)

    expect(await screen.findByText(/have not analysed any photos yet/)).toBeInTheDocument()
  })

  it('renders a thumbnail and the score for each analysis', async () => {
    signedInAs()
    respondWithPage({
      items: [item({ id: 'a1', overall_score: 91, image_url: '/media/analyses/a1.jpg' })],
      next_cursor: null,
    })
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    const thumbnail = await screen.findByRole('img', { name: /Photo analysed on/ })
    expect(thumbnail).toHaveAttribute('src', '/media/analyses/a1.jpg')
    expect(screen.getByText('91 / 100')).toBeInTheDocument()
  })

  it('describes an unscored analysis without inventing a number', async () => {
    signedInAs()
    respondWithPage({
      items: [item({ overall_score: null, pose_detected: true })],
      next_cursor: null,
    })
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    expect(await screen.findByText(/Not enough was visible to score/)).toBeInTheDocument()
  })

  it('says plainly when no person was detected, rather than a blank or zero score', async () => {
    signedInAs()
    respondWithPage({
      items: [item({ pose_detected: false, overall_score: null })],
      next_cursor: null,
    })
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    expect(await screen.findByText('No person detected')).toBeInTheDocument()
  })

  it('renders the trend sparkline fed from its own endpoint', async () => {
    signedInAs()
    respondWithPage({ items: [item()], next_cursor: null })
    respondWithTrend({
      points: [
        { created_at: '2026-01-01T00:00:00Z', value: 12, status: 'ok', rules_version: '1.0.0' },
      ],
    })
    renderWithProviders(<History />)

    expect(await screen.findByTestId('sparkline')).toBeInTheDocument()
  })

  it('loads the next page on request and appends rather than replacing', async () => {
    signedInAs()
    server.use(
      http.get(ANALYSES_ENDPOINT, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        return cursor === null
          ? HttpResponse.json({ items: [item({ id: 'a1' })], next_cursor: 'page2' })
          : HttpResponse.json({ items: [item({ id: 'a2' })], next_cursor: null })
      }),
    )
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    const list = await screen.findByRole('list')
    await within(list).findByRole('img', { name: /Photo analysed on/ })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() => expect(within(list).getAllByRole('listitem')).toHaveLength(2))
    // The page that answered `cursor=page2` had no further page.
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('has no "Load more" control once the last page has loaded', async () => {
    signedInAs()
    respondWithPage({ items: [item()], next_cursor: null })
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    await screen.findByRole('img', { name: /Photo analysed on/ })
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('reports a failure to load without pretending the history is empty', async () => {
    signedInAs()
    server.use(
      http.get(ANALYSES_ENDPOINT, () =>
        HttpResponse.json(
          { type: 't', title: 'x', status: 500, detail: 'Something broke.' },
          { status: 500 },
        ),
      ),
    )
    respondWithTrend({ points: [] })
    renderWithProviders(<History />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something broke.')
    expect(screen.queryByText(/have not analysed any photos yet/)).not.toBeInTheDocument()
  })
})
