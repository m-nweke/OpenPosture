import { expect, test, type Page } from '@playwright/test'

/**
 * One real journey through the production bundle.
 *
 * Deliberately not a scaffold spec. The Vue app shipped an untouched generated `vue.spec.ts`
 * asserting the text "You did it!" — it had never been run, and would have failed the moment
 * anyone tried. A spec that nobody executes is worse than no spec, because it looks like
 * coverage. That is also why this one is wired into CI (`web-e2e`) rather than left as a local
 * convenience.
 *
 * It tests the *guard and the session*, not the auth implementation — which is exactly why the
 * three auth calls below are mocked at the network layer rather than hitting a real API. This job
 * (`web-e2e` in `pr.yml`) serves `vite preview` on its own, with no backend behind it; the
 * `web-e2e-stack` journey in `e2e.yml` is what exercises the real API end to end. OP-57 swapped
 * the in-memory placeholder provider for one that makes real `fetch` calls, which is what turned
 * this from "needs nothing" into "needs this mock" — the comment that used to be here promised
 * this file would survive that swap unchanged, and this is the one adjustment it actually needed.
 */
function fakeJwt(sub: string): string {
  const segment = (payload: unknown) => Buffer.from(JSON.stringify(payload)).toString('base64url')
  return `${segment({ alg: 'none', typ: 'JWT' })}.${segment({ sub })}.`
}

/**
 * Fakes just enough of the auth API for the guard-and-session journey below: a session that does
 * not exist until registration succeeds, then does, then does not again after sign-out — tracked
 * with one flag rather than a real server, because a real server is not what this spec is about.
 */
async function mockAuthApi(page: Page): Promise<void> {
  const token = fakeJwt('e2e-user')
  let sessionActive = false

  await page.route('**/api/v1/auth/refresh', (route) =>
    route.fulfill(
      sessionActive
        ? {
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ access_token: token, token_type: 'bearer' }),
          }
        : {
            status: 401,
            contentType: 'application/problem+json',
            body: JSON.stringify({
              type: 'about:blank',
              title: 'Unauthorized',
              status: 401,
              detail: 'No session.',
            }),
          },
    ),
  )
  await page.route('**/api/v1/auth/register', (route) => {
    sessionActive = true
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: token, token_type: 'bearer' }),
    })
  })
  await page.route('**/api/v1/auth/logout', (route) => {
    sessionActive = false
    return route.fulfill({ status: 204, body: '' })
  })
}

test('a visitor must register before the dashboard will open', async ({ page }) => {
  await mockAuthApi(page)
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
