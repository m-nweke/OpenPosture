import styles from './HelloWorld.module.css'

/**
 * The landing header.
 *
 * It used to call `axios.get('http://127.0.0.1:5000/')` on mount and render whatever came back —
 * a hardcoded host, pointing at a Flask sanity route that returned the string "Hello World" and
 * no longer exists. That one line is the concrete reason the Vite proxy exists (OP-43): an API
 * address baked into a component works on exactly one machine.
 *
 * The call is deleted rather than repointed. It fetched nothing this page needs, and the app now
 * has exactly one way to reach the API — `src/api/client.ts`, over a relative URL.
 */
export default function HelloWorld({ msg }: { msg: string }) {
  return (
    <div className={styles.greetings}>
      <h1 className={styles.orange}>{msg}</h1>
      <h3>
        A virtual posture assessment tool created by graduate student developers from the University
        of Missouri-Kansas City
      </h3>
    </div>
  )
}
