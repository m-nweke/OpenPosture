import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

/**
 * One journey through the entire application: browser, Vite proxy, API, pose backend, rules
 * engine, response, render.
 *
 * **The assertion is an exact measured value, and that is the whole point.** Asserting that a
 * results panel appeared would have passed against the hardcoded constants OP-44 deleted —
 * `POSTURE_DETECTION_RESULT` rendered after a five-second timer and looked like a working app.
 * Asserting that `32°` is on screen proves a specific number travelled the full chain and was
 * computed, not typed.
 *
 * That number is exact rather than approximate because the fake backend'"'"'s `hunchback` preset is
 * an analytic stick figure: the trunk is built at 32° and the engine measures 32°. A real model
 * would force a tolerance, and a tolerance is where flake starts.
 *
 * Fake backend for the same reason `containers.yml` uses one: the job proves the layers are
 * wired together, and real inference would make it slow and its failures about something else.
 * Real-model validation lives in the `workflow_dispatch` job from OP-21.
 */

const FIXTURE = fileURLToPath(new URL('../../../fixtures/images/desk_hunch.jpeg', import.meta.url))

/** The dashboard is behind `ProtectedRoute`, so every journey starts by making an account. */
async function register(page: import('@playwright/test').Page, name: string) {
  await page.goto('/register')
  await page.getByLabel('Name:').fill(name)
  await page.getByLabel('Email:').fill(`${name.toLowerCase().replace(/\s+/g, '.')}@example.com`)
  await page.getByLabel('Password:').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page.getByRole('heading', { name: `Hello, ${name}` })).toBeVisible()
}

test('a photograph produces a real, measured result on screen', async ({ page }) => {
  await register(page, 'Ada Lovelace')

  await page.getByLabel(/Input an image of you sitting/).setInputFiles(FIXTURE)
  await page.getByRole('button', { name: 'Submit' }).click()

  await expect(page.getByRole('heading', { name: 'Your results' })).toBeVisible()

  // The exact value. 32° is the trunk inclination the `hunchback` preset is built at, so this
  // fails if any link in the chain is broken *or* if the engine's answer changes.
  await expect(page.getByText('32°')).toBeVisible()
  await expect(page.getByText(/Your torso is leaning 32° forward/)).toBeVisible()

  // The score is derived from the findings, so it is a second independent value off the same
  // pipeline rather than a restatement of the first.
  await expect(page.getByText('70')).toBeVisible()
})

test('the skeleton overlay is drawn over the uploaded photo', async ({ page }) => {
  await register(page, 'Grace Hopper')

  await page.getByLabel(/Input an image of you sitting/).setInputFiles(FIXTURE)
  await page.getByRole('button', { name: 'Submit' }).click()
  await expect(page.getByRole('heading', { name: 'Your results' })).toBeVisible()

  const canvas = page.getByTestId('skeleton')
  await expect(canvas).toBeVisible()

  // Sized to the photo rather than left at the HTML default of 300x150, which is what an overlay
  // that never measured its box would be. Checked in the real browser because the sizing depends
  // on layout and `devicePixelRatio` — the things jsdom cannot tell us about.
  const box = await canvas.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.width).toBeGreaterThan(0)

  const image = page.getByRole('img', { name: 'The photo you uploaded' })
  const imageBox = await image.boundingBox()
  expect(imageBox).not.toBeNull()
  // The overlay sits on the photo. A few pixels of tolerance for sub-pixel layout rounding.
  expect(Math.abs(box!.x - imageBox!.x)).toBeLessThan(2)
  expect(Math.abs(box!.width - imageBox!.width)).toBeLessThan(2)
})

test('an unreadable file is refused with a message the user can act on', async ({ page }) => {
  await register(page, 'Alan Turing')

  await page.getByLabel(/Input an image of you sitting/).setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('this is not an image, it is a sentence'),
  })
  await page.getByRole('button', { name: 'Submit' }).click()

  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).toContainText(/could not be decoded as an image/)
  // No half-rendered results beside the error.
  await expect(page.getByRole('heading', { name: 'Your results' })).toBeHidden()
})
