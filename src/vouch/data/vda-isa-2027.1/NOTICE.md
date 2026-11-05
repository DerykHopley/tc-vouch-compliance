# VDA ISA 2027.1: licence and credit

This folder holds material from the **VDA Information Security Assessment (VDA ISA), version 2027.1 (2026-09-30)**, in its English and German editions.

English edition:

- `isa2027.1-en.xlsx`: the ISA Excel, unchanged, as downloaded from the ENX Association's TISAX downloads (<https://portal.enx.com>).
- `controls.yaml`: twelve controls of its "Information Security" tab (1.1.1, 1.6.1, 1.6.2, 2.1.3, 4.1.2, 4.1.3, 4.2.1, 5.1.1, 5.1.2, 5.2.4, 5.2.5, 5.2.9): each control question, its requirements (must, should, and the additional requirements for high and very high protection needs) and its ISO 27001 reference. They are copied word for word from the Excel by `vouch.isa_extract`. Only the "+" bullet markers and the spaces and empty lines after each bullet's last word are left out (16 bullets end with them in the Excel, for example 5.2.4 S1), and each bullet is stored as its own entry. Line breaks and spaces inside a bullet are kept. No text is changed, translated or added. Vouch's own fields (requirement ids such as `M1`, column names) sit beside the ISA text, not inside it.

German edition (the ENX Association's own German version of the same ISA, not a translation by Vouch):

- `isa2027.1-de.xlsx`: the German ISA Excel ("ISA2027.1 (DE)"), unchanged, as downloaded from the ENX Association's TISAX downloads (<https://portal.enx.com/de-de/TISAX/downloads/>, file `isa2027-de.xlsx`). Its cover sheet reads "Version: 2027.1 | 2026-09-30".
- `controls-de.yaml`: the same twelve controls from its "Informationssicherheit" tab, copied word for word by `vouch.isa_extract` in the same way as `controls.yaml`. A requirement cell saying "Keine" has no requirements, as "None" does in English. Each control has the same requirements per column as in the English edition, so the requirement ids are the same in both files.

A compliance profile whose answer language is German (`de`) reads `controls-de.yaml`; every other language reads `controls.yaml`.

## Credit

© 2026 ENX Association, an Association according to the French Law of 1901, registered under No. w923004198 at the Sous-préfecture of Boulogne-Billancourt, France.

The VDA ISA is a work of ENX's Working Group ISA, provided to the VDA (Verband der Automobilindustrie e. V.) by the ENX Association and published by the VDA. The "Data Protection" tab of each Excel ("Datenschutz" in German) is owned and copyrighted by the VDA. Vouch uses none of that tab's content.

## Licence

The VDA ISA is licensed under the **Creative Commons Attribution-NoDerivatives 4.0 International Public License (CC BY-ND 4.0)**: <https://creativecommons.org/licenses/by-nd/4.0/>. Its complete text is in the English Excel's "License" sheet and, in German, in the German Excel's "Lizenz" sheet, which carries the same licence and the same Section 9 ("Verbreitung von Bearbeitungen"). Section 9 adds a right that is not part of the Creative Commons licence: a modified version may be distributed only if it is clearly marked as a modified version not approved by the licensor, the licence terms are unchanged, and the licensor's logos and trademarks are removed.

The licensed material is provided as-is, without warranties of any kind; see Section 5 (Disclaimer of Warranties and Limitation of Liability) of the licence.

Vouch is not connected with, sponsored or endorsed by the ENX Association or the VDA. Any paraphrase Vouch writes of an ISA text lives in its own field, never in place of the original. An edited copy of ISA text must be marked "derivative not approved by the licensor".
