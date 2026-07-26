import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { renderWithProviders } from './test/renderWithProviders'

function signedIn() {
  window.sessionStorage.setItem(
    'openposture.session',
    JSON.stringify({ id: 'u1', email: 'ada@example.com', displayName: 'Ada' }),
  )
}

describe('App', () => {
  it('renders the nav on the home route', async () => {
    renderWithProviders(<App />)

    expect(await screen.findByRole('link', { name: 'Home' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('hides the sign-out button when nobody is signed in', async () => {
    renderWithProviders(<App />)
    await screen.findByRole('link', { name: 'Home' })

    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  })

  it('shows the sign-out button to a signed-in user', async () => {
    signedIn()
    renderWithProviders(<App />)

    expect(await screen.findByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('signs the user out and returns them home', async () => {
    const user = userEvent.setup()
    signedIn()
    renderWithProviders(<App />, { route: '/dashboard' })

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))

    // The button disappearing is the observable proof that shared auth state updated — App no
    // longer keeps its own copy, so this is really asserting the provider drives the header.
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  })

  it('sends a signed-out visitor from /dashboard to the login page', async () => {
    renderWithProviders(<App />, { route: '/dashboard' })

    // End to end through the real guard: ProtectedRoute redirects, the /login route renders.
    expect(
      await screen.findByRole('heading', { name: /Log into your PostureProfile/ }),
    ).toBeInTheDocument()
  })

  it('lets a signed-in user reach the dashboard', async () => {
    signedIn()
    renderWithProviders(<App />, { route: '/dashboard' })

    expect(await screen.findByRole('heading', { name: 'Hello, Ada' })).toBeInTheDocument()
  })
})
