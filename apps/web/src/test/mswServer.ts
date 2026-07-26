import { setupServer } from 'msw/node'
import { handlers } from './handlers'

/** Node-side MSW instance. The browser worker is not needed until e2e wants offline runs. */
export const server = setupServer(...handlers)
