import { lazy, Suspense } from 'react'
import App from './App.tsx'
import type { AuthConfig } from './boot.ts'

// Clerk's package is its own chunk, fetched only in clerk mode (#32).
const ClerkApp = lazy(() => import('./ClerkApp.tsx'))

/** The app as the API's auth mode needs it: behind Clerk's sign-in, or as it is locally. */
export default function Root({ auth }: { auth: AuthConfig }) {
  if (auth.mode === 'local') return <App />
  return (
    <Suspense fallback={null}>
      <ClerkApp publishableKey={auth.publishableKey} />
    </Suspense>
  )
}
