import { NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { useAuth } from './auth'
import ProtectedRoute from './components/ProtectedRoute'
import HomeView from './views/HomeView'
import Dashboard from './views/Dashboard'
import History from './views/History'
import Login from './views/auth/Login'
import Registration from './views/auth/Registration'
import logo from './assets/openPose.png'
import styles from './App.module.css'
import { cx } from './ui/cx'

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

  // `NavLink` rather than `Link`: it sets `aria-current="page"` on the active route, which is
  // both the accessible signal and what the underline below hangs off. Doing it with a manual
  // `useLocation` comparison is the same work, done less reliably.
  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    isActive ? cx(styles.navLink, styles.navLinkActive) : cx(styles.navLink)

  return (
    <>
      {/* Visible only on keyboard focus. Without it, every keyboard user tabs through the whole
          nav on every page before reaching the content. */}
      <a className={cx(styles.skipLink)} href="#main">
        Skip to content
      </a>

      <header className={cx(styles.header)}>
        <div className={cx(styles.headerInner)}>
          <NavLink to="/" className={cx(styles.brand)}>
            <img
              alt=""
              aria-hidden="true"
              className={cx(styles.logo)}
              src={logo}
              width={36}
              height={36}
            />
            <span className={cx(styles.brandName)}>OpenPosture</span>
          </NavLink>

          <nav className={cx(styles.nav)} aria-label="Main">
            <NavLink className={navLinkClass} to="/">
              Home
            </NavLink>
            <NavLink className={navLinkClass} to="/dashboard">
              Dashboard
            </NavLink>
            <NavLink className={navLinkClass} to="/history">
              History
            </NavLink>
            {isLoggedIn ? (
              <button
                className={cx('button', 'buttonSecondary', styles.navButton)}
                onClick={handleSignOut}
              >
                Sign out
              </button>
            ) : (
              <>
                <NavLink className={navLinkClass} to="/login">
                  Login
                </NavLink>
                <NavLink className={cx('button', 'buttonPrimary', styles.navButton)} to="/register">
                  Register
                </NavLink>
              </>
            )}
          </nav>
        </div>
      </header>

      <main id="main" className="page">
        {/* Routes are declared inline, as JSX, right where they render. */}
        <Routes>
          <Route path="/" element={<HomeView />} />
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
          <Route
            path="/history"
            element={
              <ProtectedRoute>
                <History />
              </ProtectedRoute>
            }
          />
        </Routes>
      </main>

      <footer className={cx(styles.footer)}>
        <p>OpenPosture measures angles and reports what it measured. It is not a medical device.</p>
      </footer>
    </>
  )
}
