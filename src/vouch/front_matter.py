"""Front matter: the YAML block between `---` lines at the top of an evidence document."""

import yaml

_FENCE = "---"
_BOM = "﻿"


def split_front_matter(text: str) -> tuple[dict | None, str]:
    """The front matter as a mapping, and the body after its closing line.

    A leading byte-order mark is dropped. With no closed block at the top, the data is None
    and the body is the whole text. A closed block that isn't a YAML mapping is still cut
    from the body, with data None, so its lines never pass for document text: that includes
    a block YAML can't load at all, malformed or holding an impossible date (2026-13-01).
    """
    lines = text.removeprefix(_BOM).splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != _FENCE:
        return None, text
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == _FENCE:
            body = "".join(lines[end + 1 :])
            try:
                data = yaml.safe_load("".join(lines[1:end]))
            except (yaml.YAMLError, ValueError):  # ValueError: an impossible unquoted date
                return None, body
            return (data if isinstance(data, dict) else None), body
    return None, text
