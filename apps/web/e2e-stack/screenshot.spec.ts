import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

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

test('capture a real result for the README', async ({ page }) => {
  // A wide-ish viewport at 2x: the image lands in a README at roughly half size, so a 1x capture
  // looks soft on any modern display.
  await page.setViewportSize({ width: 1000, height: 1400 })

  await page.goto('/register')
  await page.getByLabel('Name:').fill('Ada')
  await page.getByLabel('Email:').fill('ada@example.com')
  await page.getByLabel('Password:').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page.getByRole('heading', { name: 'Hello, Ada' })).toBeVisible()

  await page.getByLabel(/Input an image of you sitting/).setInputFiles(FIXTURE)
  await page.getByRole('button', { name: 'Submit' }).click()

  await expect(page.getByRole('heading', { name: 'Your results' })).toBeVisible()

  // Proof the numbers are real before they are photographed: `mediapipe`, not `fake`.
  const measurements = page.getByRole('heading', { name: 'Measurements' })
  await expect(measurements).toBeVisible()
  await expect(page.getByRole('heading', { name: 'What we noticed' })).toBeVisible()
  await expect(page.getByTestId('skeleton')).toBeVisible()

  // The overlay draws on an image load event, so give the canvas a frame to settle before
  // capturing — otherwise the screenshot can catch the photo without its skeleton.
  await page.waitForTimeout(500)

  await page.screenshot({ path: OUTPUT, fullPage: true })
})
