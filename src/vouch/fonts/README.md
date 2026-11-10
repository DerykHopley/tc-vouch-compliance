# Fonts for the readiness report's PDF

`vouch.report_pdf` embeds these with fpdf2. They are the app's type (`docs/design.md`, Type), as static TrueType files (#352). DejaVu Sans draws any character one of them lacks.

| File | Role | Source | Licence |
|---|---|---|---|
| `LibreCaslonDisplay-Regular.ttf` | the title and section headings, uppercase; the letterhead's name | google/fonts, `ofl/librecaslondisplay` | `OFL-LibreCaslonDisplay.txt` |
| `Manrope-Regular.ttf`, `Manrope-Bold.ttf` | body text and tables | instances at weights 400 and 700 of google/fonts' `ofl/manrope/Manrope[wght].ttf`, cut with fontTools' instancer (the family names no Reserved Font Name) | `OFL-Manrope.txt` |
| `IBMPlexMono-Regular.ttf` | control, requirement and document ids, model and prompt names | google/fonts, `ofl/ibmplexmono`, unmodified | `OFL-IBMPlex.txt` |
| `IBMPlexSerif-MediumItalic.ttf` | quoted evidence | google/fonts, `ofl/ibmplexserif`, unmodified | `OFL-IBMPlex.txt` |
| `DejaVuSans.ttf`, `DejaVuSans-Bold.ttf` | the fallback for a character the fonts above lack | DejaVu | `LICENSE` |
