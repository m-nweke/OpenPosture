/**
 * Past analyses: thumbnails and a trend line, over the E6 cursor-paginated list.
 *
 * Two requests happen independently on mount — the first page of the list, and the full
 * trunk-inclination trend from its own single-query endpoint — because they answer different
 * questions. The list is deliberately one page at a time; the trend wants every point that
 * exists, which is the reason it is not derived from whatever page happens to be loaded.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getTrunkInclinationTrend, listAnalyses } from '../api/client'
import type { AnalysisListItem, TrendPoint } from '../api/types'
import Sparkline from './Sparkline'
import styles from './History.module.css'
import { cx } from '../ui/cx'

type Status = 'loading' | 'ready' | 'error'

export default function History() {
  const [items, setItems] = useState<AnalysisListItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [trend, setTrend] = useState<TrendPoint[] | null>(null)
  const [status, setStatus] = useState<Status>('loading')
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    Promise.all([
      listAnalyses({ signal: controller.signal }),
      getTrunkInclinationTrend(controller.signal),
    ])
      .then(([page, series]) => {
        // Superseded — StrictMode's dev-only mount/cleanup/remount runs this effect twice, and
        // the first pass's request is aborted by its own cleanup below. Checking this specific
        // request's own signal, rather than a persistent "is the component still here" flag, is
        // what keeps that first pass from clobbering the second, real one — a flag set once in
        // an unmount cleanup has no way to know a later mount should turn it back on.
        if (controller.signal.aborted) return
        setItems(page.items)
        setNextCursor(page.next_cursor)
        setTrend(series.points)
        setStatus('ready')
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError('Something went wrong loading your history.', { status: 0 }),
        )
        setStatus('error')
      })

    return () => controller.abort()
  }, [])

  const loadMoreAbortRef = useRef<AbortController | null>(null)
  useEffect(() => {
    return () => loadMoreAbortRef.current?.abort()
  }, [])

  const loadMore = useCallback(async () => {
    if (nextCursor === null || loadingMore) return

    const controller = new AbortController()
    loadMoreAbortRef.current = controller
    setLoadingMore(true)
    try {
      const page = await listAnalyses({ cursor: nextCursor, signal: controller.signal })
      if (controller.signal.aborted) return
      setItems((prev) => [...prev, ...page.items])
      setNextCursor(page.next_cursor)
    } catch (caught) {
      if (controller.signal.aborted) return
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError('Could not load more of your history.', { status: 0 }),
      )
    } finally {
      if (loadMoreAbortRef.current === controller) loadMoreAbortRef.current = null
      if (!controller.signal.aborted) setLoadingMore(false)
    }
  }, [nextCursor, loadingMore])

  return (
    <div className={styles.container}>
      <h1>Your history</h1>

      {status === 'loading' && <p className={styles.loading}>Loading your history…</p>}

      {status === 'error' && error && (
        <div className={styles.error} role="alert">
          <h2>We could not load your history</h2>
          <p>{error.message}</p>
          {error.requestId && (
            <p className={styles.requestId}>
              Reference: <code>{error.requestId}</code>
            </p>
          )}
        </div>
      )}

      {status === 'ready' && (
        <>
          <section className={styles.trendCard} aria-labelledby="trend-heading">
            <h2 id="trend-heading">Trunk inclination over time</h2>
            <Sparkline points={trend ?? []} />
          </section>

          {items.length === 0 ? (
            <p className={styles.empty}>
              You have not analysed any photos yet. Upload one from the dashboard to start your
              history.
            </p>
          ) : (
            <>
              <ul className={styles.list}>
                {items.map((item) => (
                  <li key={item.id} className={styles.item}>
                    <img
                      src={item.image_url}
                      alt={`Photo analysed on ${formatDate(item.created_at)}`}
                      className={styles.thumbnail}
                      loading="lazy"
                    />
                    <div className={styles.itemBody}>
                      <p className={styles.itemDate}>{formatDate(item.created_at)}</p>
                      <p className={styles.itemScore}>{describeScore(item)}</p>
                    </div>
                  </li>
                ))}
              </ul>

              {nextCursor !== null && (
                <button
                  className={cx('button', 'buttonSecondary')}
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                >
                  {loadingMore ? 'Loading…' : 'Load more'}
                </button>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

function describeScore(item: AnalysisListItem): string {
  if (!item.pose_detected) return 'No person detected'
  if (item.overall_score === null) return 'Not enough was visible to score'
  return `${Math.round(item.overall_score)} / 100`
}

function formatDate(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
