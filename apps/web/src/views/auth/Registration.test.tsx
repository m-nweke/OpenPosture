import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import Registration from './Registration'
import { renderWithProviders } from '../../test/renderWithProviders'

function renderRegistration() {
  return renderWithProviders(
    <Routes>
      <Route path="/register" element={<Registration />} />
      <Route path="/dashboard" element={<h1>Dashboard</h1>} />
    </Routes>,
    { route: '/register' },
  )
}

async function fillForm(
  user: ReturnType<typeof userEvent.setup>,
  { name = 'Ada Lovelace', email = 'ada@example.com', password = 'correct-horse' } = {},
) {
  await user.type(await screen.findByLabelText('Name:'), name)
  await user.type(screen.getByLabelText('Email:'), email)
  await user.type(screen.getByLabelText('Password:'), password)
  await user.click(screen.getByRole('button', { name: 'Create account' }))
}

describe('Registration', () => {
  it('creates an account and lands on the dashboard', async () => {
    const user = userEvent.setup()
    renderRegistration()

    await fillForm(user)

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('rejects a password under eight characters, inline', async () => {
    const user = userEvent.setup()
    renderRegistration()

    await fillForm(user, { password: 'short' })

    // The Firebase version raised these through `alert()`, a modal carrying raw vendor text that
    // the user had to dismiss. Inline and in our own words now.
    expect(await screen.findByRole('alert')).toHaveTextContent('at least 8 characters')
    expect(screen.queryByRole('heading', { name: 'Dashboard' })).not.toBeInTheDocument()
  })

  it('lets native constraint validation reject a malformed email before submit', async () => {
    const user = userEvent.setup()
    renderRegistration()

    const email = await screen.findByLabelText('Email:')
    await user.type(email, 'not-an-email')

    // See the equivalent note in Login.test.tsx — the browser blocks this, so our own
    // 'invalid-email' branch is provider-level defence rather than a UI path.
    expect((email as HTMLInputElement).checkValidity()).toBe(false)
  })

  it('treats a name of only spaces as no name given', async () => {
    const user = userEvent.setup()
    renderRegistration()

    await user.type(await screen.findByLabelText('Name:'), '   ')
    await user.type(screen.getByLabelText('Email:'), 'ada@example.com')
    await user.type(screen.getByLabelText('Password:'), 'correct-horse')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    // Registration still succeeds — a blank name is not an error, it just becomes null so the
    // dashboard's fallback greeting fires instead of rendering "Hello, ".
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('offers no third-party sign-in button', async () => {
    renderRegistration()
    await screen.findByLabelText('Name:')

    expect(screen.queryByRole('button', { name: /google/i })).not.toBeInTheDocument()
  })

  it('links to the login page for people who already have an account', async () => {
    renderRegistration()

    expect(await screen.findByRole('link', { name: 'Sign in here' })).toHaveAttribute(
      'href',
      '/login',
    )
  })
})
