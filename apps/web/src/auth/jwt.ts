/**
 * Reads the `sub` claim out of an access token without verifying it.
 *
 * Verifying is the server's job — `decode_access_token` in the API does that on every protected
 * request. The client only ever handles a token the server just handed it directly (register,
 * login and refresh all return one in the response body), so there is nothing here for a forged
 * signature to gain: the client would just be reading a claim off a token it already trusted
 * because it arrived over that response.
 */
export function decodeAccessTokenSubject(token: string): string | null {
  const [, payload] = token.split('.')
  if (payload === undefined) return null

  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
    const claims: unknown = JSON.parse(atob(padded))
    if (
      typeof claims === 'object' &&
      claims !== null &&
      'sub' in claims &&
      typeof claims.sub === 'string'
    ) {
      return claims.sub
    }
    return null
  } catch {
    return null
  }
}
