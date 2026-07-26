# Vue → React: OpenPosture Port Notes

`openpose-react/` is a line-for-line port of `openpose-vue/`. Same Firebase
project, same Flask API, same styling, same (still mocked) dashboard. The point
is that anything that differs between the two trees is a *framework* difference,
not a feature difference — so you can diff them directly.

Run both at once: Vue on `:5173`, React on `:5174`.

## File map

| Vue | React |
| --- | --- |
| `src/main.ts` | `src/main.tsx` + `src/firebase.ts` |
| `src/App.vue` | `src/App.tsx` + `App.module.css` |
| `src/router/index.ts` | routes inline in `App.tsx`; guard in `components/ProtectedRoute.tsx` |
| `src/components/HelloWorld.vue` | `src/components/HelloWorld.tsx` |
| `src/components/TheWelcome.vue` | `src/components/TheWelcome.tsx` |
| `src/components/WelcomeItem.vue` | `src/components/WelcomeItem.tsx` |
| `src/components/icons/*.vue` | `src/components/icons/*.tsx` |
| `src/views/*.vue` | `src/views/*.tsx` |

## The eight differences that actually matter

### 1. Reactivity is opt-in vs. re-run-everything

Vue's `ref()` wraps a value in a proxy. You mutate `.value` and Vue knows
exactly which DOM nodes depend on it. The component function runs **once**.

React re-runs the *entire component function* on every state change and diffs
the result. `useState` gives you `[value, setValue]` — there is no `.value`, and
assigning to a variable does nothing.

```ts
// Vue                          // React
const n = ref(0)                const [n, setN] = useState(0)
n.value++                       setN(n + 1)
```

This one fact explains most of the others.

### 2. `computed` vs. plain expressions

Vue's `computed()` caches until a dependency changes. React has no such thing by
default — you just compute the value in the body, because the body reruns
anyway. `useMemo` exists but is a performance tool, not a correctness one.

In `Dashboard`, the Vue `computed()`s had no reactive dependencies at all, so
the React port hoisted them to module-level constants (`POSTURE_DETECTION_RESULT`,
`WORKOUT_RESULT`). Recomputing a constant on each render is pure waste.

### 3. `v-model` doesn't exist — controlled inputs instead

The biggest day-to-day change. Vue gives you two-way binding for free:

```html
<input v-model="email" />
```

React makes you wire both directions:

```tsx
<input value={email} onChange={(e) => setEmail(e.target.value)} />
```

Verbose, but there's exactly one place state can change. See `views/auth/Login.tsx`.

### 4. Directives vs. JavaScript

| Vue | React |
| --- | --- |
| `v-if="cond"` | `{cond && <div/>}` |
| `v-else` | ternary `{cond ? <a/> : <b/>}` |
| `v-for="x in xs" :key="x"` | `{xs.map(x => <li key={x}>…</li>)}` |
| `@click="fn"` | `onClick={fn}` |
| `:src="url"` | `src={url}` |

There is no template language. JSX is JavaScript, so you use `map`, `&&`, and
ternaries. The `key` prop means the same thing in both.

### 5. Lifecycle → `useEffect`

`onMounted(fn)` becomes `useEffect(fn, [])`. Two real consequences:

- **Cleanup is first-class.** The function you return from `useEffect` runs on
  unmount. `App.tsx` uses this to unsubscribe from `onAuthStateChanged`; the Vue
  version never unsubscribes, which leaks a listener. `Dashboard.tsx` does the
  same for its `setTimeout`.
- **`onBeforeMount` has no equivalent.** Effects always run *after* paint, so
  `HelloWorld`'s API call can't populate before the first render — hence the
  empty-string initial state.

Also: `<StrictMode>` deliberately double-invokes effects in dev to surface
missing cleanup. That's why `HelloWorld` guards with a `cancelled` flag.

### 6. Scoped styles → CSS Modules

`<style scoped>` has no direct React equivalent. The closest thing Vite gives you
is CSS Modules: name a file `*.module.css`, import it, use `styles.className`.
Class names get hashed, so they can't leak.

Two knock-on effects:
- Hyphenated names become awkward (`styles['btn-signout']`), so the port uses
  camelCase (`styles.btnSignout`).
- Bare element selectors like `nav a { }` in a scoped block become `.nav a { }`
  — they need a class to hang off of.

Because modules are just imports, `Login` and `Registration` now share one
`AuthForm.module.css` instead of duplicating ~50 lines each.

### 7. Routing: configuration vs. composition

Vue Router is a plugin holding a route table, with a global `beforeEach` guard
that runs *before* navigation commits.

React Router is components. Routes are JSX inside `App.tsx`. There is **no
global guard** — you wrap the protected element:

```tsx
<Route path="/dashboard" element={
  <ProtectedRoute><Dashboard /></ProtectedRoute>
} />
```

The guard runs *during render*, not before navigation, so the component mounts
and then redirects. That's why `ProtectedRoute` needs an explicit `checking`
state — without it you'd flash the login redirect before Firebase reports back.

`useRouter()` → `useNavigate()`; `router.push(x)` → `navigate(x)`;
`<router-link to>` → `<Link to>`.

### 8. Slots → props holding JSX

Vue named slots:

```html
<template #icon><DocumentationIcon /></template>
```

React: a "slot" is just a prop whose value is an element.

```tsx
<WelcomeItem icon={<DocumentationIcon />} heading="Dataset Preparation">
```

Vue's *default* slot maps to the special `children` prop. There's no separate
mechanism — see `components/WelcomeItem.tsx`.

## Two small bugs the port fixes

Not framework differences, just things visible when transcribing:

- **`Registration`** called `router.push('/dashboard')` both inside the
  `updateProfile` callback and again right after it, so it could navigate before
  the display name saved — the Dashboard would greet "Hello, undefined". The
  React version awaits the profile update.
- **`App`** never unsubscribed its auth listener (see #5).

Both still exist in the Vue app. Left alone deliberately — you asked to keep it
untouched, and they're useful to compare against.

## Still mocked

Same as the Vue app: `submitImage()` waits 5 seconds and shows canned text.
There is no inference endpoint on the Flask API yet (see `RUNDOWN.md`). When one
is added, both frontends need the same wiring — a decent exercise for feeling
the difference in async state handling.
