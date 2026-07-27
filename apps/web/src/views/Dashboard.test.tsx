import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import Dashboard from './Dashboard'
import { renderWithProviders } from '../test/renderWithProviders'
import { server } from '../test/mswServer'
import { allGapsReport, analysisOf, frontalViewReport, hunchbackReport } from '../test/reports'
import { ANALYSES_ENDPOINT } from '../api/client'
import type { AnalysisResponse } from '../api/types'

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
 * Queried by its label, not by `container.querySelector('input[type=file]')`. Having to reach for
 * a CSS selector was the signal that the control had no accessible name at all.
 */
function fileInput(): HTMLInputElement {
  return screen.getByLabelText(/Input an image of you sitting/i)
}

/** Pick a file and submit it, which is every test's opening move. */
async function upload() {
  const user = userEvent.setup()
  await user.upload(fileInput(), pngFile())
  await user.click(screen.getByRole('button', { name: 'Analyse my posture' }))
  return user
}

function respondWith(body: AnalysisResponse, status = 201) {
  server.use(http.post(ANALYSES_ENDPOINT, () => HttpResponse.json(body, { status })))
}

/** A handler that does not answer until the returned `release` is called. */
function heldResponse() {
  let release: (() => void) | undefined
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  server.use(
    http.post(ANALYSES_ENDPOINT, async () => {
      await held
      return HttpResponse.json(analysisOf(hunchbackReport()), { status: 201 })
    }),
  )
  return () => release?.()
}

describe('Dashboard', () => {
  it('greets the signed-in user by name', async () => {
    signedInAs('Ada Lovelace')
    renderWithProviders(<Dashboard />)

    expect(await screen.findByRole('heading', { name: 'Hello, Ada Lovelace' })).toBeInTheDocument()
  })

  describe('the fake results are gone', () => {
    it('shows nothing until a real response arrives', async () => {
      // The old component rendered two hardcoded paragraphs after a five-second timer. The
      // strongest statement of this ticket is that no result exists before the server sends one.
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)

      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
      expect(screen.queryByText(/we have some recommendations/i)).not.toBeInTheDocument()
    })

    it('renders values that came from the response, not from the bundle', async () => {
      signedInAs('Ada')
      // A score no constant in this repo contains. If it appears on screen it came over the wire.
      respondWith(analysisOf(hunchbackReport({ overall_score: 43 })))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByText('43')).toBeInTheDocument()
    })
  })

  describe('uploading', () => {
    it('shows a progress bar while the request is in flight', async () => {
      signedInAs('Ada')
      const release = heldResponse()
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByRole('progressbar')).toBeInTheDocument()
      release()
      await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
    })

    it('disables submit while a request is in flight', async () => {
      signedInAs('Ada')
      const release = heldResponse()
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByRole('button', { name: 'Analysing…' })).toBeDisabled()
      release()
      await screen.findByRole('heading', { name: 'Your results' })
    })

    it('cannot be submitted with no file chosen', async () => {
      signedInAs('Ada')
      renderWithProviders(<Dashboard />)

      expect(await screen.findByRole('button', { name: 'Analyse my posture' })).toBeDisabled()
    })
  })

  describe('a successful analysis', () => {
    it('renders the score, the findings and the measurements', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByRole('heading', { name: 'Your results' })).toBeInTheDocument()
      expect(screen.getByText('70')).toBeInTheDocument()
      expect(screen.getByText(/Your torso is leaning 32°/)).toBeInTheDocument()
      expect(screen.getByText('32°')).toBeInTheDocument()
      expect(screen.getByText(/Assessed 4 of 4 measurements/)).toBeInTheDocument()
    })

    it('labels each finding with its severity', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findAllByText('major')).toHaveLength(2)
    })

    it('says so plainly when nothing was wrong', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport({ findings: [], overall_score: 100 })))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByText(/Nothing stood out/)).toBeInTheDocument()
    })
  })

  describe('an abstaining report', () => {
    it('renders gaps as an answer rather than an error', async () => {
      // The whole argument of the rules engine. A 201 with gaps is information: "we could not
      // assess your knees, try a wider shot". The original silently said "Straight back position".
      signedInAs('Ada')
      respondWith(analysisOf(allGapsReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(
        await screen.findByRole('heading', { name: 'What we could not assess' }),
      ).toBeInTheDocument()
      expect(screen.getByText(/left knee and left ankle were out of frame/)).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('refuses to invent a score when nothing could be measured', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(allGapsReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByText(/Not enough was visible to score/)).toBeInTheDocument()
      // Neither 0 nor 100 — both are confident claims about a photo the engine could not assess.
      expect(screen.queryByText('0')).not.toBeInTheDocument()
      expect(screen.queryByText('100')).not.toBeInTheDocument()
    })

    it('tells the user what would fix it', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(allGapsReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(
        await screen.findByText(/further back, with your whole body in frame/),
      ).toBeInTheDocument()
    })
  })

  describe('no person detected', () => {
    it('says so without treating it as a failure', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(null))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(
        await screen.findByRole('heading', { name: 'We could not find anyone in that photo' }),
      ).toBeInTheDocument()
      // `status`, not `alert`: the request succeeded and the answer is "nobody here".
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
    })
  })

  describe('low view confidence', () => {
    it("caveats a result using the engine's own wording, not a paraphrase", async () => {
      // "This image must be taken from a side angle" was printed by the original and never
      // enforced. Enforcing it silently would be no better; the result is shown, and labelled.
      signedInAs('Ada')
      respondWith(analysisOf(frontalViewReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByText(/looks like it was taken from the front/)).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Your results' })).toBeInTheDocument()
    })

    it('shows no caveat for a lateral photo', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)

      await upload()

      await screen.findByRole('heading', { name: 'Your results' })
      expect(screen.queryByText(/looks like it was taken from the front/)).not.toBeInTheDocument()
    })
  })

  describe('errors', () => {
    it('renders the problem detail the server sent', async () => {
      signedInAs('Ada')
      server.use(
        http.post(ANALYSES_ENDPOINT, () =>
          HttpResponse.json(
            {
              type: 'https://openposture.dev/problems/unsupported-image-type',
              title: 'Unsupported media type',
              status: 415,
              detail: 'GIF images are not supported.',
              request_id: 'abc123',
            },
            { status: 415 },
          ),
        ),
      )
      renderWithProviders(<Dashboard />)

      await upload()

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('GIF images are not supported.')
    })

    it('shows the request id so a failure can be reported precisely', async () => {
      signedInAs('Ada')
      server.use(
        http.post(ANALYSES_ENDPOINT, () =>
          HttpResponse.json(
            { type: 't', title: 'x', status: 500, detail: 'boom', request_id: 'trace-me' },
            { status: 500 },
          ),
        ),
      )
      renderWithProviders(<Dashboard />)

      await upload()

      expect(await screen.findByText('trace-me')).toBeInTheDocument()
    })

    it('survives a response that is not JSON at all', async () => {
      // An HTML 502 from something in front of the API. "Unexpected token < in JSON" is not a
      // message a user can act on.
      signedInAs('Ada')
      server.use(
        http.post(ANALYSES_ENDPOINT, () =>
          HttpResponse.text('<html>502 Bad Gateway</html>', { status: 502 }),
        ),
      )
      renderWithProviders(<Dashboard />)

      await upload()

      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('The server responded with 502.')
    })

    it('clears a previous result when a new upload fails', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('heading', { name: 'Your results' })

      server.use(
        http.post(ANALYSES_ENDPOINT, () =>
          HttpResponse.json(
            { type: 't', title: 'x', status: 400, detail: 'nope' },
            { status: 400 },
          ),
        ),
      )
      await user.upload(fileInput(), pngFile())
      await user.click(screen.getByRole('button', { name: 'Analyse my posture' }))

      await screen.findByRole('alert')
      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
    })
  })

  describe('a second file chosen mid-upload', () => {
    it('does not let the first response win', async () => {
      // Without aborting on file change, the in-flight request resolves into state beside the
      // *new* file's preview — one photo shown with another photo's measurements.
      signedInAs('Ada')
      const release = heldResponse()
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('progressbar')

      await user.upload(fileInput(), pngFile())
      release()

      await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
    })
  })

  describe('cancelling', () => {
    it('clearing mid-upload does not report a failure', async () => {
      // Aborting rejects the request, and an unfiltered catch turns that into a red alert saying
      // "The upload was cancelled." on a form the user has just deliberately cleared — telling
      // them something went wrong when nothing did.
      signedInAs('Ada')
      const release = heldResponse()
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('progressbar')

      await user.click(screen.getByRole('button', { name: 'Clear' }))
      release()

      await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('choosing another file mid-upload does not report a failure', async () => {
      signedInAs('Ada')
      const release = heldResponse()
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('progressbar')

      await user.upload(fileInput(), pngFile())
      release()

      await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
    })
  })

  describe('clearing', () => {
    it('empties the file input, so the same photo can be chosen again', async () => {
      // Clearing React state leaves the native input holding its value: the filename stays on
      // screen, and re-picking the *same* file fires no change event at all, so the form looks
      // stuck. The element has to be reset too.
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('heading', { name: 'Your results' })

      await user.click(screen.getByRole('button', { name: 'Clear' }))

      expect(fileInput().value).toBe('')

      // And the same file can be chosen again, which is the behaviour that was broken.
      await user.upload(fileInput(), pngFile())
      expect(screen.getByRole('button', { name: 'Analyse my posture' })).toBeEnabled()
    })

    it('removes the result and re-disables submit', async () => {
      signedInAs('Ada')
      respondWith(analysisOf(hunchbackReport()))
      renderWithProviders(<Dashboard />)
      const user = await upload()
      await screen.findByRole('heading', { name: 'Your results' })

      await user.click(screen.getByRole('button', { name: 'Clear' }))

      expect(screen.queryByRole('heading', { name: 'Your results' })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Analyse my posture' })).toBeDisabled()
    })
  })
})
