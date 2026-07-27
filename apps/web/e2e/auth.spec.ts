import { expect, test } from '@playwright/test'

/**
 * One real journey through the production bundle.
 *
 * Deliberately not a scaffold spec. The Vue app shipped an untouched generated `vue.spec.ts`
 * asserting the text "You did it!" — it had never been run, and would have failed the moment
 * anyone tried. A spec that nobody executes is worse than no spec, because it looks like
 * coverage. That is also why this one is wired into CI (`web-e2e`) rather than left as a local
 * convenience.
 *
 * It tests the *guard and the session*, not the auth implementation, so it should survive Epic E
 * replacing the in-memory provider with the real API unchanged.
 */
test('a visitor must register before the dashboard will open', async ({ page }) => {
  await page.goto('/dashboard')

  // ProtectedRoute redirects rather than flashing protected content.
  await expect(page.getByRole('heading', { name: /Log into your PostureProfile/ })).toBeVisible()

  await page.goto('/register')
  await page.getByLabel('Name:').fill('Ada Lovelace')
  await page.getByLabel('Email:').fill('ada@example.com')
  await page.getByLabel('Password:').fill('correct-horse')
  await page.getByRole('button', { name: 'Create account' }).click()

  await expect(page.getByRole('heading', { name: 'Hello, Ada Lovelace' })).toBeVisible()

  // The session survives a full reload — the browser-level version of the sessionStorage
  // restore that auth.test.tsx checks in jsdom.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Hello, Ada Lovelace' })).toBeVisible()

  await page.getByRole('button', { name: 'Sign out' }).click()
  await page.goto('/dashboard')
  await expect(page.getByRole('heading', { name: /Log into your PostureProfile/ })).toBeVisible()
})
