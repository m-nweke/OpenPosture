import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import ProtectedRoute from './ProtectedRoute'
import { renderWithProviders } from '../test/renderWithProviders'
import { mockSignedIn } from '../test/authFixtures'

/** The guard in a router, so a redirect is observable as a route change rather than mocked. */
function renderGuarded(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<p>Login page</p>} />
      <Route
        path="/secret"
        element={
          <ProtectedRoute>
            <p>Secret content</p>
          </ProtectedRoute>
        }
      />
    </Routes>,
    { route },
  )
}

describe('ProtectedRoute', () => {
  it('shows the checking state before the session is known', () => {
    renderGuarded('/secret')

    // Neither outcome yet. Rendering "Secret content" here would flash protected material at a
    // signed-out visitor; redirecting here would bounce a signed-in one to /login.
    expect(screen.getByText('Checking sign-in…')).toBeInTheDocument()
    expect(screen.queryByText('Secret content')).not.toBeInTheDocument()
    expect(screen.queryByText('Login page')).not.toBeInTheDocument()
  })

  it('redirects to /login once the session resolves to nobody', async () => {
    renderGuarded('/secret')

    expect(await screen.findByText('Login page')).toBeInTheDocument()
    expect(screen.queryByText('Secret content')).not.toBeInTheDocument()
  })

  it('renders its children once a session is restored', async () => {
    mockSignedIn({ id: 'u1', email: 'ada@example.com', displayName: 'Ada' })

    renderGuarded('/secret')

    expect(await screen.findByText('Secret content')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('Checking sign-in…')).not.toBeInTheDocument()
    })
  })
})
