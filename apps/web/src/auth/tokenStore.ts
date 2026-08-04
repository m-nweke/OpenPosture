/**
 * The access token's one true home.
 *
 * A module-level variable, not React state — on purpose. `api/client.ts` is a plain function
 * module, not a component: it needs to read the current token on every request and learn when
 * the token changes (sign-in, rotation, sign-out) without being able to call a hook. A `useState`
 * inside the provider cannot serve that need, because nothing outside a component can read a
 * hook's state. So the token lives here, and `ApiAuthProvider` treats this module as the source
 * of truth rather than the other way around.
 *
 * Nothing here ever touches a durable Web Storage API — see noTokenStorage.test.tsx, which scans
 * for exactly that. The token dies with the tab, which is the whole point: anything durable is
 * readable by any script that runs on the page.
 */

export type AccessTokenListener = (token: string | null) => void

let accessToken: string | null = null
const listeners = new Set<AccessTokenListener>()

export function getAccessToken(): string | null {
  return accessToken
}

/** Updates the token and tells every subscriber, in particular the provider watching for null. */
export function setAccessToken(token: string | null): void {
  accessToken = token
  for (const listener of listeners) listener(token)
}

/**
 * Notified on every `setAccessToken` call, including the one a failed refresh makes.
 *
 * This is what lets a 401 discovered deep inside `api/client.ts` end the session the provider is
 * showing, without `client.ts` importing React or the provider polling a module it has no other
 * reason to know about.
 */
export function subscribeToAccessToken(listener: AccessTokenListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
