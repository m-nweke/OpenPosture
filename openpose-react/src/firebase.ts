import { initializeApp } from 'firebase/app'
import { getAuth } from 'firebase/auth'
import { getFirestore } from 'firebase/firestore'

// Same Firebase project as the Vue app — both frontends talk to `openpose-db`.
const firebaseConfig = {
  apiKey: 'AIzaSyAiBggE1bmfuZUGmSWoxwrtJ8lKBExdCFU',
  authDomain: 'openpose-db.firebaseapp.com',
  projectId: 'openpose-db',
  storageBucket: 'openpose-db.appspot.com',
  messagingSenderId: '271005034064',
  appId: '1:271005034064:web:ccd3e84835151e502b1355',
  measurementId: 'G-DTH1R02B5M',
}

// NOTE (vs. Vue): the Vue app initializes Firebase inside `main.ts` and exports
// `auth`/`db` from the same file that mounts the app, so components do
// `import { auth } from '@/main'`. That works but couples every component to the
// entry point. Here it lives in its own module instead.
const firebaseApp = initializeApp(firebaseConfig)

export const auth = getAuth(firebaseApp)
export const db = getFirestore(firebaseApp)
