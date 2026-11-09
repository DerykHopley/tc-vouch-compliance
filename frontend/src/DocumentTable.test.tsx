import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DocumentTable from './DocumentTable.tsx'
import type { LibraryDocument } from './setup.ts'

const BACKUP: LibraryDocument = {
  id: 'backup-policy', source: 'policies', title: 'Backup policy', kind: 'documented', version: '1.0',
  approval_date: '2024-02-12', review_cycle: 'yearly',
  stale_because: 'approved 2024-02-12 with a yearly review, due 2025-02-12', stale: true,
}
const UNDATED: LibraryDocument = {
  id: 'draft-note', source: 'policies', title: 'draft-note', kind: 'documented', version: null,
  approval_date: null, review_cycle: null, stale_because: 'no review date', stale: true,
}
const TRAINING: LibraryDocument = {
  id: 'training-record', source: 'records', title: 'Security training record 2026', kind: 'implemented',
  version: '2026-10', approval_date: '2026-10-05', review_cycle: 'yearly', stale_because: null, stale: false,
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
    expect(within(backup).getByRole('rowheader').textContent).toBe('Backup policybackup-policy')
    expect(cells(backup).slice(0, 4)).toEqual(['documented', '1.0', '2024-02-12', 'yearly'])
    const stale = within(backup).getByText('Stale')
    expect(stale.className).toBe('flag')
    expect(stale.getAttribute('title')).toBe('approved 2024-02-12 with a yearly review, due 2025-02-12')
    expect(cells(backup)[4]).toBe('Stale: approved 2024-02-12 with a yearly review, due 2025-02-12')
    expect(within(backup).getByText('documented').className).toBe('kind kind-documented')
    expect(cells(undated).slice(1, 4)).toEqual(['', 'no date', 'none'])
    expect(within(training).getByText('implemented').className).toBe('kind kind-implemented')
    expect(cells(training)[4]).toBe('Current')
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
