import { afterEach } from 'vitest'
import { endDoubleClickGuard } from './doubleClickGuard.ts'

// The in-app ask's double-click guard (#527) outlives the ask by design, so a test's last pointer confirm would
// swallow the next test's first click in the same document: each test starts without one.
afterEach(endDoubleClickGuard)
