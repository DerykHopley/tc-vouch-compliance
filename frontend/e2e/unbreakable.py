"""Free texts holding a token no hyphenation can break (#530's verify, #538), shared by the report's,
the working paper's and the chat's checks: each widened its column or box past its room while the
text broke words only at hyphens or spaces."""

# A link, a signed link (a browser may break a link after a slash, not in its id and signature), a
# file name under 31 characters, and a German compound in Nordlicht's English report.
UNBREAKABLE = {
    "link": "Siehe https://intranet.alpenwerk.example/isms/richtlinien/"
    "Protokollierungsrichtlinie_Version3_freigegeben.pdf für Details.",
    "signed link": "Kein Nachweis unter https://intranet.alpenwerk.example/isms/dokument?"
    "id=8f14e45fceea167a5a36dedd4bea2543&sig=c9a1b2d3e4f506178f14e45fceea167a5a36dedd hinterlegt.",
    "file name": "Siehe Zugriffsmatrix_Q3_2026_v2.xlsx dazu.",
    "compound": "The Informationssicherheitsmanagementsystem was signed off.",
}

LONG_PATH = (
    "Abgelegt unter /srv/nachweise/zugriffssteuerung/"
    "Berechtigungskonzept_Rollenmatrix_Administratorkonten_2026_freigegeben.pdf zur Prüfung."
)
"""A quote's long file path (#538): Firefox may break it at its slashes, Chromium doesn't."""

LONG_QUOTES = (UNBREAKABLE["signed link"], UNBREAKABLE["link"], LONG_PATH)
"""The quotes #538's checks put in place of served ones: each widened its table or the chat's
message while a quote broke only at hyphens or spaces."""
