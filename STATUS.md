# Status

The owner checks merged changes in batches (CLAUDE.md, The Fixes lane): each UI change goes on this list when it merges, with what to try, and comes off once checked. A PR over the 400-line target is named with its size.

## Ready to check

- **#377**: the readiness report's PDF keeps ids searchable when a note holds a long unbroken string. On a working paper, reject or override with a note holding a long string without spaces or dashes (a 96-character hash), then export the report as PDF: in your PDF viewer, search for a document id (such as `access-control-policy`) and it's found; on the audit trail page only that string breaks, inside the note column, and an evidence cell listing several documents shows each id whole on its own line.
