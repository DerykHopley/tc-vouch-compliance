import type { LibraryDocument } from './setup.ts'

/** Test data (#408): the sample library's stale backup policy, as GET /api/developer/sample-library sends it, shared by
 * the DocumentTable, setup and wizard tests so they agree on one document. */
export const BACKUP_STALE = 'approved 2024-02-12 with a yearly review, due 2025-02-12'
export const BACKUP_POLICY: LibraryDocument = {
  id: 'backup-policy', source: 'policies', title: 'Backup policy', kind: 'documented', version: '1.0',
  approval_date: '2024-02-12', review_cycle: 'yearly', stale_because: BACKUP_STALE, stale: true,
}
