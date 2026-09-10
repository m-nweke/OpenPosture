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
import { HistoryIcon, HomeIcon, RegisterIcon, SignInIcon, SignOutIcon, UploadIcon } from './ui/icons'

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
  const railLinkClass = ({ isActive }: { isActive: boolean }) =>
    isActive ? cx(styles.railLink, styles.railLinkActive) : cx(styles.railLink)

  return (
    <div className={cx(styles.shell)}>
      {/* Visible only on keyboard focus. Without it, every keyboard user tabs through the whole
          rail on every page before reaching the content. */}
      <a className={cx(styles.skipLink)} href="#main">
        Skip to content
      </a>

      <nav className={cx(styles.rail)} aria-label="Main">
        <NavLink to="/" className={cx(styles.brand)} aria-label="OpenPosture home">
          <img alt="" aria-hidden="true" className={cx(styles.logo)} src={logo} width={24} height={24} />
        </NavLink>

        {/* Icon-only by design — the rail stays a constant width regardless of route. The label
            text is not decorative, so it stays in the DOM for a screen reader and appears
            on hover/focus as a tooltip rather than being dropped. */}
        <NavLink className={railLinkClass} to="/" title="Home">
          <HomeIcon className={cx(styles.railIcon)} />
          <span className="sr-only">Home</span>
        </NavLink>
        <NavLink className={railLinkClass} to="/dashboard" title="Dashboard">
          <UploadIcon className={cx(styles.railIcon)} />
          <span className="sr-only">Dashboard</span>
        </NavLink>
        <NavLink className={railLinkClass} to="/history" title="History">
          <HistoryIcon className={cx(styles.railIcon)} />
          <span className="sr-only">History</span>
        </NavLink>

        <div className={cx(styles.railSpacer)} />

        {isLoggedIn ? (
          <button className={cx(styles.railLink, styles.railAction)} onClick={handleSignOut} title="Sign out">
            <SignOutIcon className={cx(styles.railIcon)} />
            <span className="sr-only">Sign out</span>
          </button>
        ) : (
          <>
            <NavLink className={railLinkClass} to="/login" title="Login">
              <SignInIcon className={cx(styles.railIcon)} />
              <span className="sr-only">Login</span>
            </NavLink>
            <NavLink className={railLinkClass} to="/register" title="Register">
              <RegisterIcon className={cx(styles.railIcon)} />
              <span className="sr-only">Register</span>
            </NavLink>
          </>
        )}
      </nav>

      <div className={cx(styles.column)}>
        <main id="main" className={cx(styles.page)}>
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
      </div>
    </div>
  )
}
