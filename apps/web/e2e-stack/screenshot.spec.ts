import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { ANALYSES_ENDPOINT } from '../src/api/client'

/**
 * Captures the README screenshot from a real result.
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
 * It asserts before it captures. A screenshot of a broken page is worse than none, because it
 * ships looking authoritative.
 */

const FIXTURE = fileURLToPath(new URL('../../../fixtures/images/desk_hunch.jpeg', import.meta.url))
const OUTPUT = fileURLToPath(new URL('../../../docs/images/dashboard-result.png', import.meta.url))

// A wide-ish viewport at a real 2x: the image lands in a README at roughly half size, so a 1x
// capture looks soft on any modern display.
//
// `deviceScaleFactor` is a *context* option — `page.setViewportSize()` changes the CSS viewport and
// leaves the device pixel ratio at 1, so an earlier version of this file claimed 2x and captured
// 1x. It also means the overlay's `devicePixelRatio` handling is exercised for real here.
test.use({ viewport: { width: 1000, height: 1400 }, deviceScaleFactor: 2 })

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

  await page.screenshot({ path: OUTPUT, fullPage: true })
})
