import { describe, expect, it } from 'vitest'
import { getAccessToken, setAccessToken, subscribeToAccessToken } from './tokenStore'

// setupTests.ts resets the token store after every test — module-level state outlives any one
// test, so without that this file's own tests would start from whatever the previous one left.

describe('tokenStore', () => {
  it('starts with no access token', () => {
    expect(getAccessToken()).toBeNull()
  })

  it('returns whatever was last set', () => {
    setAccessToken('token-a')
    expect(getAccessToken()).toBe('token-a')

    setAccessToken('token-b')
    expect(getAccessToken()).toBe('token-b')

    setAccessToken(null)
    expect(getAccessToken()).toBeNull()
  })

  it('notifies subscribers of every change, with the new value', () => {
    const seen: Array<string | null> = []
    const unsubscribe = subscribeToAccessToken((token) => seen.push(token))

    setAccessToken('token-a')
    setAccessToken(null)

    expect(seen).toEqual(['token-a', null])
    unsubscribe()
  })

  it('stops notifying once unsubscribed', () => {
    const seen: Array<string | null> = []
    const unsubscribe = subscribeToAccessToken((token) => seen.push(token))

    unsubscribe()
    setAccessToken('token-a')

    expect(seen).toEqual([])
  })
})
