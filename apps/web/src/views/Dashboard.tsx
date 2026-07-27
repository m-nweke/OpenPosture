/**
 * The upload flow.
 *
 * This file used to render two hardcoded strings behind a five-second `setTimeout` that imitated
 * thinking. Nothing was uploaded and no model was ever invoked. Deleting that is the point of
 * Epic D: the number on this screen now describes the person who uploaded the photo.
 *
 * The states are one enum rather than a set of booleans, because they are genuinely exclusive and
 * the combinations a boolean pair allows — uploading *and* showing results — are ones this screen
 * should never be in.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth'
import { ApiError, analysePosture } from '../api/client'
import type { AnalysisResponse } from '../api/types'
import PostureResult from './PostureResult'
import styles from './Dashboard.module.css'
import { cx } from '../ui/cx'

type Status = 'idle' | 'uploading' | 'done' | 'error'

export default function Dashboard() {
  // ProtectedRoute guarantees a user by the time this renders, but the type does not know that,
  // so the fallback stays. "Hello, there" beats "Hello, " if that assumption ever breaks.
  const { user } = useAuth()
  const name = user?.displayName ?? 'there'

  const [file, setFile] = useState<File | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [progress, setProgress] = useState(0)
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  // The input element itself, because clearing React state does not clear it. A file input
  // keeps its value until the element is reset, so the filename stays on screen and
  // re-picking the *same* file fires no change event — the form looks stuck.
  const inputRef = useRef<HTMLInputElement | null>(null)

  // An in-flight upload outlives the component otherwise, and its state update on return would
  // warn about updating an unmounted component — the same class of leak the old `setTimeout`
  // cleanup guarded against, for a request rather than a timer.
  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  // Object URLs hold the file in memory until revoked. Left unrevoked, uploading twenty photos in
  // one session leaks all twenty.
  useEffect(() => {
    if (!file) {
      setImageUrl(null)
      return
    }
    const url = URL.createObjectURL(file)
    setImageUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    // Abort anything still in flight. Without this, picking a second file while the first is
    // uploading leaves the first request running — and when it resolves it writes its report
    // into state beside the *new* file's preview. The user then sees one photo with another
    // photo's measurements, which is worse than either an error or a wait.
    abortRef.current?.abort()
    setFile(event.target.files?.[0] ?? null)
    setAnalysis(null)
    setError(null)
    setStatus('idle')
    setProgress(0)
  }

  const submit = useCallback(async () => {
    if (!file || status === 'uploading') return

    const controller = new AbortController()
    abortRef.current = controller

    setStatus('uploading')
    setProgress(0)
    setError(null)
    setAnalysis(null)

    try {
      const result = await analysePosture(file, {
        onProgress: setProgress,
        signal: controller.signal,
      })
      // Superseded while in flight. Writing the result now would put this report beside whatever
      // the screen has moved on to — a cleared form, or a different photo's preview.
      if (controller.signal.aborted) return
      setAnalysis(result)
      setStatus('done')
    } catch (caught) {
      // A cancelled upload is not a failure to report. Clearing the form and choosing a second
      // file both abort the request, and both already put the screen where the user asked for
      // it; surfacing "The upload was cancelled." as a red alert on top of that tells them
      // something went wrong when nothing did.
      if (controller.signal.aborted) return

      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError('Something went wrong analysing that photo.', { status: 0 }),
      )
      setStatus('error')
    } finally {
      // Only if this request still owns the ref. A superseded request finishing later would
      // otherwise clear the *current* one's controller, leaving it with no way to be aborted.
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [file, status])

  const clear = () => {
    abortRef.current?.abort()
    if (inputRef.current) inputRef.current.value = ''
    setFile(null)
    setAnalysis(null)
    setError(null)
    setStatus('idle')
    setProgress(0)
  }

  return (
    <div className={styles.container}>
      <h1>Hello, {name}</h1>

      <div className={styles.card}>
        {/* Promoted from a <p> to the field's actual label, so a screen reader reads the
            requirements when the input takes focus rather than announcing a bare file button. */}
        <label className={styles.instructions} htmlFor="posture-image">
          Input an image of you sitting for posture evaluation. This image must be taken from a side
          angle.
        </label>
        <input
          ref={inputRef}
          type="file"
          id="posture-image"
          accept="image/jpeg,image/png,image/webp"
          onChange={handleFileChange}
          className={styles.fileInput}
        />
        <div className={styles.actions}>
          <button
            className={cx('button', 'buttonPrimary')}
            onClick={submit}
            disabled={!file || status === 'uploading'}
          >
            {status === 'uploading' ? 'Analysing…' : 'Analyse my posture'}
          </button>
          <button className={cx('button', 'buttonDanger')} onClick={clear} disabled={!file}>
            Clear
          </button>
        </div>
      </div>

      {status === 'uploading' && (
        <div className={styles.uploading}>
          {/* A real <progress> element, driven by bytes actually sent. The browser announces it
              to assistive technology for free, which a styled div does not. */}
          <label htmlFor="upload-progress">Uploading your photo</label>
          <progress id="upload-progress" max={100} value={progress}>
            {progress}%
          </progress>
          <span>{progress}%</span>
        </div>
      )}

      {status === 'error' && error && (
        <div className={styles.error} role="alert">
          <h2>We could not analyse that photo</h2>
          <p>{error.message}</p>
          {error.requestId && (
            // Disclosed on purpose: it is the one piece of internal state that is safe to share,
            // and the only thing that lets a user's report be joined to the server logs.
            <p className={styles.requestId}>
              Reference: <code>{error.requestId}</code>
            </p>
          )}
        </div>
      )}

      {status === 'done' && analysis && !analysis.pose_detected && (
        <div className={styles.noPerson} role="status">
          <h2>We could not find anyone in that photo</h2>
          <p>
            The upload worked, but no person was detected. Try a photo where your whole upper body
            is visible, taken from the side.
          </p>
        </div>
      )}

      {status === 'done' && analysis?.report && (
        <PostureResult
          report={analysis.report}
          imageUrl={imageUrl}
          landmarks={analysis.landmarks}
        />
      )}
    </div>
  )
}
