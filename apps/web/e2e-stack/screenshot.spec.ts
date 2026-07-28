import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { ANALYSES_ENDPOINT } from '../src/api/client'

/**
 * Captures both README screenshots: the landing page, and a real analysis result.
 *
 * Not part of the journey suite — it is a tool, run on demand:
 *
 *     npm run screenshot
 *
 * against a stack running the **real** MediaPipe backend, so the numbers in the image are
 * measured from an actual photograph rather than produced by the fake preset. A README whose
 * headline image came from a fabricated skeleton would be the same species of dishonesty as the
 * hardcoded results this epic deleted.
 *
 * Both live here so one command regenerates every image the README shows, and neither drifts
 * out of date on its own after a frontend change.
 *
 * It asserts before it captures. A screenshot of a broken page is worse than none, because it
 * ships looking authoritative.
 */

const FIXTURE = fileURLToPath(new URL('../../../fixtures/images/desk_hunch.jpeg', import.meta.url))
const OUTPUT = fileURLToPath(new URL('../../../docs/images/dashboard-result.jpg', import.meta.url))
const OUTPUT_LANDING = fileURLToPath(new URL('../../../docs/images/landing.png', import.meta.url))

// A wide-ish viewport at 1.25x. GitHub renders README images at roughly 880px, so 1.25x is enough
// oversampling to stay sharp on a retina display without paying for pixels nobody sees. This was
// 2x, which produced a 3 MB headline image — the README's single heaviest asset, downloaded by
// everyone who opens the repository.
//
// `deviceScaleFactor` is a *context* option — `page.setViewportSize()` changes the CSS viewport and
// leaves the device pixel ratio at 1, so an earlier version of this file claimed 2x and captured
// 1x. It also means the overlay's `devicePixelRatio` handling is exercised for real here.
test.use({ viewport: { width: 1000, height: 1400 }, deviceScaleFactor: 1.25 })

test('capture the landing page for the README', async ({ page }) => {
  // Wider than the dashboard capture and only as tall as it needs to be. The feature grid is
  // `repeat(auto-fit, minmax(16rem, 1fr))`, so the three cards only sit in one row above roughly
  // 900px of content width — narrower and they wrap, which is the wrong picture for a README.
  await page.setViewportSize({ width: 1100, height: 1000 })
  await page.goto('/')

  await expect(page.getByRole('heading', { name: /measured.*not guessed/i })).toBeVisible()

  // Named rather than counted: asserting three `article`s would still pass if the grid wrapped
  // them into two rows, or if a card rendered empty.
  const features = page.getByRole('region', { name: 'How it works' })
  for (const card of [
    'Upload a side-on photo',
    'Seven measurements',
    'Findings, and honest gaps',
  ]) {
    await expect(features.getByRole('heading', { name: card })).toBeVisible()
  }

  // Clip to halfway between the feature grid and the card below it. Captured to the viewport edge
  // instead, the frame ends partway through that card — three complete cards plus a slice of a
  // fourth, which reads as a screenshot someone forgot to crop. A fixed padding below the grid
  // does the same thing whenever the section gap is smaller than the padding, so measure the gap
  // and land in the middle of it.
  const privacy = page.getByRole('region', { name: 'Where your photo goes' })
  const [box, next] = [await features.boundingBox(), await privacy.boundingBox()]
  if (!box || !next)
    throw new Error('landing sections have no layout box — the page did not render')

  const gridBottom = box.y + box.height
  await page.screenshot({
    path: OUTPUT_LANDING,
    clip: { x: 0, y: 0, width: 1100, height: (gridBottom + next.y) / 2 },
  })
})

test('capture a real result for the README', async ({ page }) => {
  await page.goto('/register')
  await page.getByLabel('Name:').fill('Ada')
  await page.getByLabel('Email:').fill('ada@example.com')
  await page.getByLabel('Password:').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Hello, Ada' })).toBeVisible()

  await page.getByLabel(/Input an image of you sitting/).setInputFiles(FIXTURE)

  // The response the *page* received, not a second request. Asserting on a separate API call
  // would prove the stack can run mediapipe, not that the numbers about to be photographed came
  // from it.
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(ANALYSES_ENDPOINT) && r.request().method() === 'POST',
    ),
    page.getByRole('button', { name: 'Analyse my posture' }).click(),
  ])

  await expect(page.getByRole('heading', { name: 'Your results' })).toBeVisible()

  // The assertion the earlier version only claimed in a comment: this really is the real backend.
  // Without it the spec passes happily against `POSE_BACKEND=fake` and produces a README image of
  // a fabricated skeleton — the same species of dishonesty as the hardcoded results this epic
  // deleted, with a photograph attached.
  const body = await response.json()
  expect(body.report.backend).toBe('mediapipe')
  expect(body.pose_detected).toBe(true)
  // A measured angle, not a preset's constant. The fake backend's trunk is exactly 32.0.
  expect(body.report.metrics.trunk_inclination_deg.status).toBe('ok')
  expect(body.report.metrics.trunk_inclination_deg.value).not.toBe(32)

  await expect(page.getByRole('heading', { name: 'Measurements' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What we noticed' })).toBeVisible()
  await expect(page.getByTestId('skeleton')).toBeVisible()

  // The overlay draws on an image load event, so give the canvas a frame to settle before
  // capturing — otherwise the screenshot can catch the photo without its skeleton.
  await page.waitForTimeout(500)

  // JPEG, unlike the landing capture's PNG. Most of this frame is a photograph, and a photograph
  // in a lossless format is what made the old image 3 MB: at this same 1250px width, PNG is
  // 1547 KB against 607 KB here. The landing page stays PNG because it is flat colour and type,
  // where PNG is both smaller and exact.
  await page.screenshot({ path: OUTPUT, type: 'jpeg', quality: 95, fullPage: true })
})
