import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GoogleAuthProvider, signInWithEmailAndPassword, signInWithPopup } from 'firebase/auth'
import { auth } from '../../firebase'
import styles from './AuthForm.module.css'

export default function Login() {
  // Vue's v-model is two-way binding. React has no two-way binding — you wire
  // `value` down and `onChange` back up by hand. That's the "controlled
  // component" pattern, and it's the single biggest day-to-day difference
  // between the two frameworks for form code.
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errMsg, setErrMsg] = useState('')
  const navigate = useNavigate()

  const login = () => {
    signInWithEmailAndPassword(auth, email, password)
      .then(() => {
        console.log('Successfully signed in!')
        setErrMsg('')
        navigate('/dashboard')
      })
      .catch((error) => {
        console.error(error.code)
        switch (error.code) {
          case 'auth/invalid-email':
            setErrMsg('Invalid email')
            break
          case 'auth/user-not-found':
            setErrMsg('No account with that email was found')
            break
          case 'auth/wrong-password':
            setErrMsg('Incorrect password')
            break
          default:
            setErrMsg('Email or password incorrect')
            break
        }
      })
  }

  const signInWithGoogle = () => {
    const provider = new GoogleAuthProvider()
    signInWithPopup(auth, provider).then((result) => {
      console.log(result.user)
      navigate('/dashboard')
    })
  }

  return (
    <div className={styles.container}>
      <h1>Log into your PostureProfile</h1>
      <div className={styles.form}>
        <div className={styles.formGroup}>
          <label htmlFor="email">Email:</label>
          <input
            type="email"
            id="email"
            placeholder="Enter your email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className={styles.formGroup}>
          <label htmlFor="password">Password:</label>
          <input
            type="password"
            id="password"
            placeholder="Enter your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {errMsg && <p className={styles.error}>{errMsg}</p>}
        <button style={{ backgroundColor: 'orange', color: '#ffffff' }} onClick={login}>
          Log in
        </button>
      </div>
      <p className={styles.separator}>or</p>
      <button className={styles.btnGoogle} onClick={signInWithGoogle}>
        Sign in with Google
      </button>
    </div>
  )
}
