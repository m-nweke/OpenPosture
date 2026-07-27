import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import HelloWorld from './HelloWorld'

describe('HelloWorld', () => {
  it('renders the message it was given', () => {
    render(<HelloWorld msg="OpenPosture" />)

    expect(screen.getByRole('heading', { name: 'OpenPosture', level: 1 })).toBeInTheDocument()
  })

  it('makes no network request', () => {
    // It used to fetch a Flask route at a hardcoded host on mount. MSW is configured to error on
    // unhandled requests, so a reintroduced call would fail this test rather than pass silently.
    render(<HelloWorld msg="OpenPosture" />)

    expect(screen.getByText(/graduate student developers/)).toBeInTheDocument()
  })
})
