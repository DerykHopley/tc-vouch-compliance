# Status

The owner checks merged changes in batches (CLAUDE.md, The Fixes lane): each UI change goes on this list when it merges, with what to try, and comes off once checked. A PR over the 400-line target is named with its size.

## Ready to check

- **#462**: the Ask Vouch button never covers what you're working on. At 1440, 1024 and 390 wide, in light and dark, on the Overview, a working paper (with a remediation item's Due date showing), the Report and the Developer page: scroll to the bottom, and the last line ends above Ask Vouch with a gap; Tab through the fields, and none stops under the button. As a page first opens, the button may still float over a line of text; that's the decided reading on the ticket.
- **#465**: pinning the chat keeps the page usable. On a working paper with an assessment: at 1440 wide, open Ask Vouch, Make larger, Pin beside the page; the chat opens about 398px wide (the widest that leaves the page its 1042px), the top bar stays one row and the requirements table shows every column, nothing cut at the chat's edge. Drag the chat's edge left: it stops there. Narrow the window below 1402px: the chat drops back to enlarged and Pin isn't offered (at 1024 it never is). In every chat state at 1440 and 1024, the header's tag reads "on 5.2.4 · Working paper" in full, under "Vouch" when it doesn't fit beside it. Try light and dark.
