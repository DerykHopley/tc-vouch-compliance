# Demo files for Add document

Sample files for showing **Add document** and **Add new version** on the Company page's Documents panel (#585, #593, #595). Choose one in the form's file field and press Add document (or Add version); each line says what you'll see.

- `clean-desk-policy.md`: a short policy with front matter (title, kind, approval date, review cycle) that fills the form; Add document adds it as "Clean Desk Policy", version 1.
- `clean-desk-policy-v2.md`: a second version of the same policy, holding one planted instruction-like line; add it through Add new version on "Clean Desk Policy": Vouch warns that it holds text reading like an instruction, and Add anyway takes it as version 2.
- `clean-desk-policy.pdf`: a one-line PDF; refused, as only Markdown (.md) and plain-text (.txt) files can be added.
- `empty.md`: an empty file; refused as empty.
- `customer-system-access-review-q2-2026.md`: a record, Nordlicht's first review of the accounts its staff hold in customers' systems (second quarter of 2026); Add document adds it as "Customer System Access Review", version 1.
- `customer-system-access-review-q3-2026.md`: the same review a quarter later; add it through Add new version on "Customer System Access Review" to make it version 2.

## What each file shows when a control is assessed (Nordlicht)

| File | Control to assess | What to expect |
|---|---|---|
| `clean-desk-policy.md` | any, e.g. 1.1.1 | Not cited: no Nordlicht control asks about clean desks. |
| `customer-system-access-review-q2-2026.md` | 4.2.1 (access rights) | Cited, mainly under M2. The built-in access review leaves out the accounts staff hold in customers' systems, which keeps M2 partly evidenced; this record covers them. |
| `customer-system-access-review-q3-2026.md` | none; added after 4.2.1 cited version 1 | 4.2.1's card says "New evidence: assess again" (#586). |

**The citation needs the live model.** The bundled fake model plays a fixed script per control that cites only Nordlicht's built-in documents, so on a local run or an owner preview it never cites an added document: assessing 4.2.1 cites the authentication standard (the default script), and the "New evidence" note never appears.

To show the citation and the note without a paid run, start the local app with the fake script kept here, which assesses 4.2.1 with a citation of the added record:

```
ASSESS_FAKE_SCRIPT=docs/demo-files/fake-script-4.2.1-cites-customer-access-review.json \
  scripts/dev.sh --api-port <spare> --web-port <spare>
```

Then add the clean desk policy and the second-quarter review, assess 4.2.1 (it cites "Customer System Access Review" version 1 and the access control policy; the clean desk policy is not cited), and add the third-quarter review as its new version: 4.2.1 shows "New evidence: assess again". This script answers every control as 4.2.1, so assess only 4.2.1 on a run that uses it.

These files are not evidence. Vouch finds evidence only in each compliance profile's own folders under `fixtures/` (the sample library among them) and in documents added through the app, so nothing here is indexed or searched.
