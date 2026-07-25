import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  signInWithPopup,
  updateProfile,
} from 'firebase/auth'
import { auth } from '../../firebase'
import styles from './AuthForm.module.css'

export default function Registration() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const navigate = useNavigate()

  const register = () => {
    createUserWithEmailAndPassword(auth, email, password)
      .then(() => {
        const user = auth.currentUser
        if (user) {
          // The Vue version calls router.push('/dashboard') both inside this
          // .then() and again immediately after, so it can navigate before the
          // display name is saved. Awaiting the profile update first avoids the
          // Dashboard rendering "Hello, undefined".
          return updateProfile(user, { displayName: name })
            .then(() => {
              console.log('Successfully Registered!')
              navigate('/dashboard')
            })
            .catch((error) => {
              console.error(error)
              alert(error.message)
            })
        }
        navigate('/dashboard')
      })
      .catch((error) => {
        console.error(error)
        alert(error.message)
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
      <h1>Create a Posture Profile</h1>
      <div className={styles.form}>
        <div className={styles.formGroup}>
          <label htmlFor="name">Name:</label>
          <input
            type="text"
            id="name"
            placeholder="Enter your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
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
        <button style={{ backgroundColor: 'orange', color: '#ffffff' }} onClick={register}>
          Submit
        </button>
      </div>
      <p className={styles.separator}>or</p>
      <button className={styles.btnGoogle} onClick={signInWithGoogle}>
        Sign in with Google
      </button>
      <p className={styles.signInHere}>
        Already have an account? <Link to="/login">Sign in here</Link>.
      </p>
    </div>
  )
}
