import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth'
import styles from './Dashboard.module.css'

// Vue computed() with no reactive dependency is really just a constant, so
// these live outside the component — recreating them each render would be waste.
const POSTURE_DETECTION_RESULT =
  'Our posture detection model detected you sitting with a reclined back, with hands not folded, ' +
  'non-kneeling, and a forward neck. This is pretty good posture, but we have some recommendations ' +
  'for improvement!'

const WORKOUT_RESULT = [
  'To fix the reclined back, openPosture recommends 90 seconds planks, 3 times a day.',
  'For the kneeling, we recommend 3x20 each leg hamstring curls twice a day (use ankle weights if available).',
  'For the forward neck, we recommend 3x20 shoulder shrugs, 3 times a day.',
]

export default function Dashboard() {
  // ProtectedRoute guarantees a user by the time this renders, but the type does not know that,
  // so the fallback stays. "Hello, there" beats "Hello, " if that assumption ever breaks.
  const { user } = useAuth()
  const name = user?.displayName ?? 'there'
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [showResults, setShowResults] = useState(false)
  const [showLoading, setShowLoading] = useState(false)

  // Vue: ref(null) on a plain value that shouldn't trigger re-render.
  // React: useRef is the escape hatch for exactly that — mutable, non-reactive.
  const imageFile = useRef<File | null>(null)
  const timerRef = useRef<number | null>(null)

  // The Vue version leaves its setTimeout running if you navigate away mid-load.
  // React makes the cleanup obvious because the effect *asks* for a teardown.
  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current)
    }
  }, [])

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
      imageFile.current = file
      const reader = new FileReader()
      reader.onload = (e) => {
        if (e.target) setImageUrl(e.target.result as string)
      }
      reader.readAsDataURL(file)
    }
  }

  const submitImage = () => {
    // TODO: still mocked, same as the Vue app — there is no inference endpoint
    // on the Flask API yet, so this just waits 5s and shows canned text.
    console.log('Submit button clicked')
    setShowLoading(true)
    timerRef.current = window.setTimeout(() => {
      setShowLoading(false)
      setShowResults(true)
    }, 5000)
  }

  const clearImage = () => {
    imageFile.current = null
    setImageUrl(null)
    setShowResults(false)
  }

  return (
    <div className={styles.container}>
      <h1>Hello, {name}</h1>
      <div className={styles.card}>
        <p className={styles.instructions}>
          Input an image of you sitting for posture evaluation. This image must be taken from a side
          angle.
        </p>
        <input
          type="file"
          accept="image/*"
          onChange={handleFileUpload}
          className={styles.fileInput}
        />
        <button className={styles.submitButton} onClick={submitImage}>
          Submit
        </button>
        <button className={styles.clearButton} onClick={clearImage}>
          Clear
        </button>
      </div>

      {/* Vue: v-if="showResults" → React: short-circuit on the condition */}
      {showResults && (
        <div className={styles.results}>
          <h2>Here are your results:</h2>
          {imageUrl && <img src={imageUrl} alt="Uploaded" className={styles.uploadedImage} />}
          <p>{POSTURE_DETECTION_RESULT}</p>
          {WORKOUT_RESULT.length > 0 && (
            <div>
              <h3>Posture Improvement Recommendations:</h3>
              <ul>
                {/* Vue: v-for with :key → React: .map() with a key prop */}
                {WORKOUT_RESULT.map((recommendation) => (
                  <li key={recommendation}>{recommendation}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {showLoading && <div className={styles.loadingSpinner} />}
    </div>
  )
}
