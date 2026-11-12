import { beforeEach, describe, expect, it } from 'vitest'
import { forgetFinishedEntries, forgetSetupEntries, leaveSetup, markSetupEntry, settleSetupEntry }
  from './setupHistory.ts'

/** Back or Forward, once the browser has moved: jsdom moves history asynchronously, then fires popstate. */
const moved = (move: () => void) => new Promise<void>((done) => {
  window.addEventListener('popstate', () => done(), { once: true })
  move()
})

beforeEach(() => {
  forgetSetupEntries()
  window.history.replaceState(null, '', '/')
})

describe('leaveSetup, issues 476 and 568', () => {
  it('replaces the setup entry when the create lands on it', () => {
    window.history.pushState(null, '', '/developer/setup')
    const entry = markSetupEntry()
    const length = window.history.length

    leaveSetup(entry, '/')

    expect(window.location.pathname).toBe('/')
    expect(window.history.length).toBe(length)
    expect(settleSetupEntry().path).toBe('/')
  })

  it('keeps a developer who clicked a tab where they went, and Back then gives the Dossier, not the wizard', async () => {
    window.history.pushState(null, '', '/developer/setup')
    const entry = markSetupEntry()
    window.history.pushState(null, '', '/company') // a top-bar tab while the create answers
    const length = window.history.length

    leaveSetup(entry, '/')

    expect(window.location.pathname).toBe('/company')
    expect(window.history.length).toBe(length) // no copy of /company to press Back past
    await moved(() => window.history.back())
    expect(settleSetupEntry().path).toBe('/')
    expect(window.location.pathname).toBe('/')
  })

  it('keeps a developer who pressed Back where they went, and Forward then gives the Dossier, not the wizard', async () => {
    window.history.pushState(null, '', '/developer')
    window.history.pushState(null, '', '/developer/setup')
    const entry = markSetupEntry()
    await moved(() => window.history.back())

    leaveSetup(entry, '/')

    expect(window.location.pathname).toBe('/developer')
    await moved(() => window.history.forward())
    expect(settleSetupEntry().path).toBe('/')
    expect(window.location.pathname).toBe('/')
  })

  it("leaves another setup's entry a wizard: each Set up a company is an entry of its own", async () => {
    window.history.pushState(null, '', '/developer/setup')
    const finished = markSetupEntry()
    window.history.pushState(null, '', '/developer')
    window.history.pushState(null, '', '/developer/setup')
    const fresh = markSetupEntry()
    expect(fresh).not.toBe(finished)
    window.history.pushState(null, '', '/company')

    leaveSetup(finished, '/')

    await moved(() => window.history.back())
    expect(settleSetupEntry().path).toBe('/developer/setup')
  })

  it('marks an entry once: the wizard opened again on it keeps its mark', () => {
    window.history.pushState(null, '', '/developer/setup')
    expect(markSetupEntry()).toBe(markSetupEntry())
  })
})

describe('leaveSetup, issue 569', () => {
  it('says whether the setup entry was shown: true when replaced now, false when the developer had left it', async () => {
    window.history.pushState(null, '', '/developer/setup')
    const shown = markSetupEntry()
    expect(leaveSetup(shown, '/')).toBe(true)

    window.history.pushState(null, '', '/developer/setup')
    const left = markSetupEntry()
    await moved(() => window.history.back())
    expect(leaveSetup(left, '/')).toBe(false)
  })

  it('names the company it opens once, when Back or Forward first reaches the finished entry', async () => {
    window.history.pushState(null, '', '/developer')
    window.history.pushState(null, '', '/developer/setup')
    const entry = markSetupEntry()
    await moved(() => window.history.back())

    leaveSetup(entry, '/', 'musterfirma-gmbh')
    expect(settleSetupEntry()).toEqual({ path: '/developer', opens: null })

    await moved(() => window.history.forward())
    expect(settleSetupEntry()).toEqual({ path: '/', opens: 'musterfirma-gmbh' })
    await moved(() => window.history.back())
    settleSetupEntry()
    await moved(() => window.history.forward())
    expect(settleSetupEntry()).toEqual({ path: '/', opens: null })
  })

  it('names none for an entry replaced as the create landed on it', () => {
    window.history.pushState(null, '', '/developer/setup')
    leaveSetup(markSetupEntry(), '/', 'musterfirma-gmbh')
    expect(settleSetupEntry()).toEqual({ path: '/', opens: null })
  })

  it('forgets every finished entry once the app that made them closes (a sign-out): Back then finds its own page',
    async () => {
    window.history.pushState(null, '', '/developer/setup')
    const entry = markSetupEntry()
    window.history.pushState(null, '', '/company')
    leaveSetup(entry, '/', 'musterfirma-gmbh')

    forgetFinishedEntries()

    await moved(() => window.history.back())
    expect(settleSetupEntry()).toEqual({ path: '/developer/setup', opens: null })
  })
})
