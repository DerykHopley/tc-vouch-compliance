import { describe, expect, it } from 'vitest'
import { controlPath, isoLabel } from './overview.ts'

describe('isoLabel', () => {
  it("shortens ISA's ISO 27001 reference to its Annex A controls", () => {
    expect(isoLabel('ISO 27001:2022: A.5.15, A.8.5')).toBe('ISO A.5.15, A.8.5')
    expect(isoLabel('ISO 27001:2022 A.5.1')).toBe('ISO A.5.1')
    expect(isoLabel('A.5.1')).toBe('A.5.1')
  })
})

describe('controlPath', () => {
  it('encodes the control id, as the address is read back decoded', () => {
    expect(controlPath('4.1.2')).toBe('/controls/4.1.2')
    expect(controlPath('a/b ?')).toBe('/controls/a%2Fb%20%3F')
  })
})
