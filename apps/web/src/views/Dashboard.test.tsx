import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Dashboard from './Dashboard'
import { renderWithProviders } from '../test/renderWithProviders'

function signedInAs(displayName: string | null) {
  window.sessionStorage.setItem(
    'openposture.session',
    JSON.stringify({ id: 'u1', email: 'ada@example.com', displayName }),
  )
}

/** Real bytes, because jsdom's FileReader will not produce a data URL from a string stub. */
function pngFile() {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'posture.png', { type: 'image/png' })
}

/**
 * Queried by its label, not by `container.querySelector('input[type=file]')`. Having to reach
 * for a CSS selector was the signal that the control had no accessible name at all — a screen
 * reader announced it as an unlabelled file button. Fixed in the markup; this now fails if the
 * label is ever unwired.
 */
function fileInput(): HTMLInputElement {
  return screen.getByLabelText(/Input an image of you sitting/i)
}

afterEach(() => {
  vi.useRealTimers()
})

describe('Dashboard', () => {
  it('greets the signed-in user by name', async () => {
    signedInAs('Ada Lovelace')
    renderWithProviders(<Dashboard />)

    expect(await screen.findByRole('heading', { name: 'Hello, Ada Lovelace' })).toBeInTheDocument()
  })

  it('falls back to a generic greeting when there is no display name', async () => {
    signedInAs(null)
    renderWithProviders(<Dashboard />)

    // Guards the `?? 'there'`. The Firebase version read `auth.currentUser?.displayName` with no
    // fallback, so a user who registered without a name saw "Hello, undefined".
    expect(await screen.findByRole('heading', { name: 'Hello, there' })).toBeInTheDocument()
  })

  it('accepts a file without showing anything until results arrive', async () => {
    const user = userEvent.setup()
    signedInAs('Ada')
    renderWithProviders(<Dashboard />)
    await screen.findByRole('heading', { name: 'Hello, Ada' })

    await user.upload(fileInput(), pngFile())

    // Documenting existing behaviour, not endorsing it: the chosen image is only rendered
    // inside the results block, so between picking a file and waiting out the fake five-second
    // analysis the user gets no confirmation their upload registered. Worth revisiting when the
    // real inference flow lands in Epic D; changing it now would be scope this ticket does not
    // own.
    expect(fileInput().files).toHaveLength(1)
    expect(screen.queryByAltText('Uploaded')).not.toBeInTheDocument()
  })

  it('shows the results, and the uploaded image, five seconds after submit', async () => {
    // `shouldAdvanceTime` keeps real time moving while the 5s timer stays controllable.
    // Without it, user-event waits on real time that a fully frozen clock never delivers and
    // the test deadlocks rather than failing.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    signedInAs('Ada')
    renderWithProviders(<Dashboard />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    await user.upload(fileInput(), pngFile())
    // FileReader resolves on a macrotask; let it settle before submitting.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await user.click(screen.getByRole('button', { name: 'Submit' }))

    expect(
      screen.queryByRole('heading', { name: 'Here are your results:' }),
    ).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })

    // The result text is still hardcoded — there is no inference endpoint until Epic D. This
    // pins the *interaction*, so it survives the real API replacing the canned copy.
    expect(screen.getByRole('heading', { name: 'Here are your results:' })).toBeInTheDocument()
    expect(screen.getByAltText('Uploaded')).toBeInTheDocument()
  })

  it('clears the image and the results', async () => {
    // `shouldAdvanceTime` keeps real time moving while the 5s timer stays controllable.
    // Without it, user-event waits on real time that a fully frozen clock never delivers and
    // the test deadlocks rather than failing.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    signedInAs('Ada')
    renderWithProviders(<Dashboard />)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    await user.upload(fileInput(), pngFile())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    await user.click(screen.getByRole('button', { name: 'Submit' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(screen.getByAltText('Uploaded')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    await waitFor(() => {
      expect(screen.queryByAltText('Uploaded')).not.toBeInTheDocument()
    })
    expect(
      screen.queryByRole('heading', { name: 'Here are your results:' }),
    ).not.toBeInTheDocument()
  })
})
