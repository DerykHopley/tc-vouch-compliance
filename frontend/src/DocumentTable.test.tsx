import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DocumentTable from './DocumentTable.tsx'
import type { TableWords } from './documents.ts'
import { BACKUP_POLICY as BACKUP, BACKUP_STALE } from './libraryFixtures.ts'
import type { LibraryDocument } from './setup.ts'
const UNDATED: LibraryDocument = {
  id: 'draft-note', source: 'policies', title: 'draft-note', kind: 'documented', version: null,
  approval_date: null, review_cycle: null, stale_because: 'no review date', stale: true, language: null,
}
const TRAINING: LibraryDocument = {
  id: 'training-record', source: 'records', title: 'Security training record 2026', kind: 'implemented',
  version: '2026-10', approval_date: '2026-10-05', review_cycle: 'yearly', stale_because: null, stale: false,
  language: 'en',
}

afterEach(cleanup)

const rows = () => within(screen.getByRole('table')).getAllByRole('row').slice(1)
const cells = (row: HTMLElement) => within(row).getAllByRole('cell').map((c) => c.textContent)

describe('the document table', () => {
  it('shows each document: title and id, kind tag, version, approval date, review cycle and status', () => {
    render(<DocumentTable caption="Policies" documents={[BACKUP, UNDATED, TRAINING]} />)
    expect(screen.getByRole('table', { name: 'Policies' })).toBeTruthy()
    expect(within(screen.getByRole('table')).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Document', 'Kind', 'Version', 'Approved', 'Review', 'Status'])
    const [backup, undated, training] = rows()
    const title = within(backup).getByRole('rowheader')
    expect(within(title).getByText('Backup policy').className).toBe('doc-title')
    expect(within(title).getByText('backup-policy').className).toBe('mono doc-id')
    expect(cells(backup).slice(0, 4)).toEqual(['documented', '1.0', '2024-02-12', 'yearly'])
    // The stale reason is shown, as the working paper shows it, not only in a tooltip or to a screen reader.
    const stale = within(backup).getByText(`Stale: ${BACKUP_STALE}`)
    expect(stale.className).toBe('flag')
    expect(stale.closest('.sr-only, [title]')).toBeNull()
    expect(cells(backup)[4]).toBe(`Stale: ${BACKUP_STALE}`)
    expect(within(backup).getByText('documented').className).toBe('kind kind-documented')
    expect(cells(undated).slice(1, 5)).toEqual(['no version', 'no date', 'none', 'Stale: no review date'])
    expect(within(training).getByText('implemented').className).toBe('kind kind-implemented')
    expect(cells(training)[4]).toBe('Current')
  })

  it('says only "Stale" when a stale document comes with no reason', () => {
    render(<DocumentTable caption="Policies" documents={[{ ...BACKUP, stale_because: null }]} />)
    expect(cells(rows()[0])[4]).toBe('Stale')
  })

  it('has no checkboxes without onToggle', () => {
    render(<DocumentTable caption="Policies" documents={[BACKUP, TRAINING]} />)
    expect(screen.queryAllByRole('checkbox')).toEqual([])
  })

  it('with onToggle, gives each document a "Choose <title>" checkbox that calls back with its id and folder', async () => {
    const onToggle = vi.fn()
    render(<DocumentTable caption="Records" documents={[BACKUP, TRAINING]}
      selected={{ policies: ['backup-policy'], records: [] }} onToggle={onToggle} />)
    const backup = screen.getByRole('checkbox', { name: 'Choose Backup policy' }) as HTMLInputElement
    const training = screen.getByRole('checkbox', { name: 'Choose Security training record 2026' }) as HTMLInputElement
    expect([backup.checked, training.checked]).toEqual([true, false])
    await userEvent.click(training)
    expect(onToggle).toHaveBeenCalledWith('training-record', 'records')
    await userEvent.click(backup)
    expect(onToggle).toHaveBeenLastCalledWith('backup-policy', 'policies')
  })
})

describe('the document table, with words and an extra column (the Documents tab, issue 424)', () => {
  const WORDS: TableWords = {
    columns: { document: 'Dokument', kind: 'Art', version: 'Version', approved: 'Freigegeben am', review: 'Überprüfung',
      status: 'Status', cited_by: 'Zitiert von' },
    kinds: { documented: 'Dokumentiert', implemented: 'Umgesetzt' },
    cycles: { quarterly: 'Vierteljährlich', 'half-yearly': 'Halbjährlich', yearly: 'Jährlich', 'two-yearly': 'Zweijährlich' },
    stale: 'Prüfung überfällig',
    stale_reasons: { no_approval_date: 'kein Freigabedatum', no_review_cycle: 'kein Prüfzyklus',
      unknown_cycle: 'unbekannter Prüfzyklus' },
    current: 'Aktuell', no_version: 'keine Version', no_date: 'kein Datum', no_cycle: 'keine',
  }
  const extra = { heading: 'Zitiert von', cell: (d: LibraryDocument) => `cell ${d.id}` }

  it('shows the given headings, the kind and cycle words, an overdue stale word alone, and the extra column', () => {
    render(<DocumentTable caption="Richtlinien" documents={[BACKUP, UNDATED, TRAINING]} words={WORDS}
      extraColumn={extra} />)
    expect(within(screen.getByRole('table')).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Dokument', 'Art', 'Version', 'Freigegeben am', 'Überprüfung', 'Status', 'Zitiert von'])
    const [backup, undated, training] = rows()
    expect(cells(backup)).toEqual(['Dokumentiert', '1.0', '2024-02-12', 'Jährlich', 'Prüfung überfällig',
      'cell backup-policy'])
    expect(within(backup).getByText('Dokumentiert').className).toBe('kind kind-documented')
    expect(within(backup).getByText('Prüfung überfällig').className).toBe('flag')
    expect(cells(undated).slice(1, 5)).toEqual(['keine Version', 'kein Datum', 'keine',
      'Prüfung überfällig: kein Freigabedatum'])
    expect(cells(training).slice(0, 5)).toEqual(['Umgesetzt', '2026-10', '2026-10-05', 'Jährlich', 'Aktuell'])
  })

  it.each([
    ['no approval date', { approval_date: null }, 'Prüfung überfällig: kein Freigabedatum'],
    ['no review cycle', { review_cycle: null }, 'Prüfung überfällig: kein Prüfzyklus'],
    ['a cycle it has no word for', { review_cycle: 'monthly' }, 'Prüfung überfällig: unbekannter Prüfzyklus'],
    ["a cycle named like an object's own member", { review_cycle: 'constructor' },
      'Prüfung überfällig: unbekannter Prüfzyklus'],
  ])('says why a stale document has %s, which its date and cycle alone don\'t explain', (_, change, status) => {
    const stale = { ...BACKUP, ...change, stale_because: 'no review date' }
    render(<DocumentTable caption="Policies" documents={[stale]} words={WORDS} />)
    expect(cells(rows()[0])[4]).toBe(status)
    expect(within(rows()[0]).getByText(status).className).toBe('flag')
  })

  it.each(['constructor', 'toString', '__proto__'])('shows a review cycle named %s as written', (cycle) => {
    render(<DocumentTable caption="Policies" documents={[{ ...TRAINING, review_cycle: cycle }]} words={WORDS} />)
    expect(cells(rows()[0])[3]).toBe(cycle)
  })

  it('shows a review cycle it has no word for as written', () => {
    render(<DocumentTable caption="Policies" documents={[{ ...TRAINING, review_cycle: 'monthly' }]} words={WORDS} />)
    expect(cells(rows()[0])[3]).toBe('monthly')
  })

  it("marks each title with its document's language, and none without one", () => {
    render(<DocumentTable caption="Policies" documents={[BACKUP, UNDATED]} />)
    const [backup, undated] = rows()
    expect(within(backup).getByText('Backup policy').closest('[lang]')?.getAttribute('lang')).toBe('en')
    expect(within(undated).getByText('draft-note', { selector: '.doc-title' }).closest('[lang]')).toBeNull()
  })
})
