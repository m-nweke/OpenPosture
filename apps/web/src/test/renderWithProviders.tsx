import type { ReactNode } from 'react'
import { render, type RenderResult } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth'

/**
 * Renders a subtree inside the same providers `main.tsx` uses.
 *
 * MemoryRouter rather than BrowserRouter because jsdom's history is shared across a file — one
 * test navigating to /dashboard would otherwise change where the next one starts. `initialEntries`
 * makes the starting route an explicit argument instead of ambient state.
 *
 * The real AuthProvider is used, not a mock. The in-memory implementation has no network and no
 * timers, so there is nothing to stub, and testing against the actual provider means these tests
 * keep their value when Epic E swaps it out.
 */
export function renderWithProviders(
  ui: ReactNode,
  { route = '/' }: { route?: string } = {},
): RenderResult {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  )
}
