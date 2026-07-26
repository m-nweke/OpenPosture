import { useEffect, useState } from 'react'
import axios from 'axios'
import styles from './HelloWorld.module.css'

// Vue: defineProps<{ msg: string }>()
// React: props are just the function's first argument.
export default function HelloWorld({ msg }: { msg: string }) {
  const [testData, setTestData] = useState('')

  // Vue used onBeforeMount (fires before the first paint). React has no
  // pre-render async hook — useEffect always runs *after* render, so the first
  // frame shows the empty string. That's why `testData` starts as ''.
  useEffect(() => {
    let cancelled = false

    axios
      .get('http://127.0.0.1:5000/')
      .then((response) => {
        if (!cancelled) setTestData(response.data)
      })
      .catch((error) => {
        console.error('Error getting data: ', error)
      })

    // StrictMode double-invokes effects in dev; this flag stops a late response
    // from writing into an unmounted component.
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className={styles.greetings}>
      <h1 className={styles.orange}>{msg}</h1>
      <h3>
        A virtual posture assessment tool created by graduate student developers from the University
        of Missouri-Kansas City
      </h3>
      <p>{testData}</p>
    </div>
  )
}
