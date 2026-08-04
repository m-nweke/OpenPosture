import { afterEach, describe, expect, it } from 'vitest'
import { getAccessToken, setAccessToken, subscribeToAccessToken } from './tokenStore'

afterEach(() => {
  // Module-level state outlives any one test, so the next file to import this module would
  // otherwise start from whatever the previous test left behind.
  setAccessToken(null)
})

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
