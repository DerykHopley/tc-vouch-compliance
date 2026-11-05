import { describe, expect, it } from 'vitest'
import { controlPath } from './overview.ts'

describe('controlPath', () => {
  it('encodes the control id, as the address is read back decoded', () => {
    expect(controlPath('4.1.2')).toBe('/controls/4.1.2')
    expect(controlPath('a/b ?')).toBe('/controls/a%2Fb%20%3F')
  })
})
