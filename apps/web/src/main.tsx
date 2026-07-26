import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import { AuthProvider } from './auth'
import './assets/main.css'

// Vue: createApp(App).use(router).mount('#app') — the router is a plugin.
// React: the router is a *component* you wrap around the tree.
//
// AuthProvider sits inside BrowserRouter because Epic E's implementation will want to redirect
// on session expiry, and router hooks only work below the router. It is the single place the
// auth implementation is named — swapping it is a one-line change here.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
