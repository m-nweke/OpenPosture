import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import HelloWorld from './HelloWorld'
import { server } from '../test/mswServer'

// No providers needed: this component takes a prop and makes one HTTP call. MSW handles the
// call, so the real axios request runs and only the response is faked.
describe('HelloWorld', () => {
  it('renders the message it is given', () => {
    render(<HelloWorld msg="OpenPosture" />)

    expect(screen.getByRole('heading', { name: 'OpenPosture' })).toBeInTheDocument()
  })

  it('shows what the API returns', async () => {
    render(<HelloWorld msg="OpenPosture" />)

    expect(await screen.findByText('Hello from the OpenPosture API')).toBeInTheDocument()
  })

  it('renders without the API text when the request fails', async () => {
    server.use(http.get('http://127.0.0.1:5000/', () => HttpResponse.error()))

    render(<HelloWorld msg="OpenPosture" />)

    // The API is not running in most dev setups and will not exist in its real form until
    // Epic D. A failed greeting must not take the header down with it.
    expect(await screen.findByRole('heading', { name: 'OpenPosture' })).toBeInTheDocument()
  })
})
