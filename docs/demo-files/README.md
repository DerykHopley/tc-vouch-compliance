# Demo files for Add document

Sample files for showing **Add document** and **Add new version** on the Company page's Documents panel (#585, #593, #595). Choose one in the form's file field and press Add document (or Add version); each line says what you'll see.

- `clean-desk-policy.md`: a short policy with front matter (title, kind, approval date, review cycle) that fills the form; Add document adds it as "Clean Desk Policy", version 1.
- `clean-desk-policy-v2.md`: a second version of the same policy, holding one planted instruction-like line; add it through Add new version on "Clean Desk Policy": Vouch warns that it holds text reading like an instruction, and Add anyway takes it as version 2.
- `clean-desk-policy.pdf`: a one-line PDF; refused, as only Markdown (.md) and plain-text (.txt) files can be added.
- `empty.md`: an empty file; refused as empty.

These files are not evidence. Vouch finds evidence only in each compliance profile's own folders under `fixtures/` (the sample library among them) and in documents added through the app, so nothing here is indexed or searched.
