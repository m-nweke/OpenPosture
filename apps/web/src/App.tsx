import { Link, Route, Routes, useNavigate } from 'react-router-dom'
import { useAuth } from './auth'
import HelloWorld from './components/HelloWorld'
import ProtectedRoute from './components/ProtectedRoute'
import HomeView from './views/HomeView'
import AboutView from './views/AboutView'
import Dashboard from './views/Dashboard'
import Login from './views/auth/Login'
import Registration from './views/auth/Registration'
import logo from './assets/openPose.png'
import styles from './App.module.css'

export default function App() {
  const navigate = useNavigate()

  // Was: a local `isLoggedIn` state plus its own `onAuthStateChanged` subscription. Both are
  // gone. The provider already tracks this, and a component that re-derives shared state from
  // the same source is a second copy that can drift from the first.
  const { user, signOut } = useAuth()
  const isLoggedIn = user !== null

  const handleSignOut = () => {
    void signOut().then(() => navigate('/'))
  }

  return (
    <>
      <header className={styles.header}>
        <img alt="OpenPose logo" className={styles.logo} src={logo} width={200} height={200} />

        <div className={styles.wrapper}>
          <HelloWorld msg="OpenPosture" />

          <nav className={styles.nav}>
            <Link className={styles.navLink} to="/">
              Home
            </Link>
            <Link className={styles.navLink} to="/dashboard">
              Dashboard
            </Link>
            <Link className={styles.navLink} to="/register">
              Register
            </Link>
            <Link className={styles.navLink} to="/login">
              Login
            </Link>
            {/* Vue: v-if="isLoggedIn"  →  React: {cond && <jsx/>} */}
            {isLoggedIn && (
              <button className={styles.btnSignout} onClick={handleSignOut}>
                Sign out
              </button>
            )}
          </nav>
        </div>
      </header>

      {/* Vue: <RouterView /> renders whatever the router config matched.
          React: routes are declared inline, as JSX, right where they render. */}
      <Routes>
        <Route path="/" element={<HomeView />} />
        <Route path="/about" element={<AboutView />} />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Registration />} />
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
      </Routes>
    </>
  )
}
