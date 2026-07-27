import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import Login from './Login'
import Registration from './Registration'
import { renderWithProviders } from '../../test/renderWithProviders'

/**
 * Login mounted with a real /dashboard route, so "did it navigate" is answered by what renders
 * rather than by a spy on useNavigate. Registration is mounted too because the only way to have
 * an account in the in-memory provider is to create one — which also means these tests exercise
 * the sign-up and sign-in paths together, the way a user meets them.
 */
function renderLogin(route = '/login') {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Registration />} />
      <Route path="/dashboard" element={<h1>Dashboard</h1>} />
    </Routes>,
    { route },
  )
}

async function registerAccount(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Name:'), 'Ada Lovelace')
  await user.type(screen.getByLabelText('Email:'), 'ada@example.com')
  await user.type(screen.getByLabelText('Password:'), 'correct-horse')
  await user.click(screen.getByRole('button', { name: 'Create account' }))
}

describe('Login', () => {
  it('signs in a registered user and sends them to the dashboard', async () => {
    const user = userEvent.setup()
    renderLogin('/register')
    await registerAccount(user)
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('reports bad credentials without saying which part was wrong', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.type(await screen.findByLabelText('Email:'), 'nobody@example.com')
    await user.type(screen.getByLabelText('Password:'), 'correct-horse')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    // Wording matters here. "No account with that email" — which the Firebase version showed —
    // confirms to an attacker which addresses exist.
    expect(await screen.findByRole('alert')).toHaveTextContent('Email or password incorrect')
    expect(screen.queryByRole('heading', { name: 'Dashboard' })).not.toBeInTheDocument()
  })

  it('lets native constraint validation reject a malformed email before submit', async () => {
    const user = userEvent.setup()
    renderLogin()

    const email = await screen.findByLabelText('Email:')
    await user.type(email, 'not-an-email')

    // type="email" inside a <form> means the browser blocks submission itself, so the request
    // never reaches our handler. That is why there is no assertion here about our own
    // 'invalid-email' message: it is provider-level defence, covered in auth.test.tsx, and
    // unreachable through the UI. Asserting it here would have tested a path users cannot take.
    expect((email as HTMLInputElement).checkValidity()).toBe(false)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('submits on Enter, not only on the button', async () => {
    const user = userEvent.setup()
    renderLogin()

    await user.type(await screen.findByLabelText('Email:'), 'nobody@example.com')
    await user.type(screen.getByLabelText('Password:'), 'wrong{Enter}')

    // The original had no <form>, so Enter did nothing and password managers had no field group
    // to recognise. This is the regression test for that.
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('offers no third-party sign-in button', async () => {
    renderLogin()
    await screen.findByLabelText('Email:')

    // "Sign in with Google" was a direct Firebase popup call. It is gone rather than disabled,
    // because Epic E decides whether self-hosted auth offers OAuth at all, and a button that
    // looks live but does nothing is worse than no button.
    expect(screen.queryByRole('button', { name: /google/i })).not.toBeInTheDocument()
  })
})
