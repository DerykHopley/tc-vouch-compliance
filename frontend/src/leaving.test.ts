import { afterEach, describe, expect, it, vi } from 'vitest'
import { leaveQuestion, mayLeave, setUnsaved } from './leaving.ts'

afterEach(() => {
  setUnsaved(0)
  vi.unstubAllGlobals()
})

describe('leaveQuestion, issue 182', () => {
  it('names one override, or the count of them', () => {
    expect(leaveQuestion(1)).toBe('You have 1 override not yet recorded. Leave and drop it?')
    expect(leaveQuestion(2)).toBe('You have 2 overrides not yet recorded. Leave and drop them?')
  })
})

describe('mayLeave, issue 182', () => {
  it('lets the lead leave without asking when nothing is unsaved', () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    expect(mayLeave()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it("asks with the paper's count, and answers what the lead chose", () => {
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    setUnsaved(2)
    expect(mayLeave()).toBe(false)
    expect(confirm).toHaveBeenCalledWith('You have 2 overrides not yet recorded. Leave and drop them?')
    confirm.mockReturnValue(true)
    expect(mayLeave()).toBe(true)
  })
})
