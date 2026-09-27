#!/usr/bin/env python3
"""Alloy source with the non-code blanked out — one lexical reading.

pc-6196 (round-10 lane C-F1): `pred_body()` in dev/alloy-gate.sh walked the
traps module counting `{` and `}` with no idea what a string literal is, so
`some "{"` inside `pred rechainedBy` raised the depth and `some "}"` inside a
later, unchecked `assert` returned it to zero there. The extracted "body" ran
past the predicate's real closing brace and swallowed a decoy clause, and the
FStatus transfer was counted as covered while `pred rechainedBy` constrained
`out.status` nowhere — 16/16, 0/4, 0/3, exit 0, with the pinned jar.

That was pc-d405's own fix failing on its own new parser: "parse to the
declaration the property is about and then ask" was implemented with a brace
counter that does not know Alloy's lexical structure. The same gap sat one
step earlier, in the comment stripping every check reads through: a `--` or
`*/` inside a string literal truncated the file, and a `pred foo` inside one
was extracted as a declaration.

So the lexical question is asked ONCE, here, and every check reads the
answer. `blank_noncode` returns the source with comment text and
string-literal CONTENTS replaced by spaces, the same length and the same
line breaks — so offsets, line numbers and every regex the gate already
writes keep working, and no brace, bracket or keyword inside a comment or a
string is visible to any of them.

Alloy's lexical rules, as this implements them:
  - `//` and `--` run to end of line; `/* ... */` does not nest.
  - `"` delimits a string literal, `\\` escapes the next character, and a
    literal does not span a line break.
  - `'` does NOT delimit anything: it is a legal character in an Alloy
    identifier (`x'`), so treating it as a quote would blank live code.
"""
from __future__ import annotations


def blank_noncode(text: str) -> str:
    """`text` with comments and string contents blanked, length preserved."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] not in ('"', "\n"):
                if text[i] == "\\" and i + 1 < n and text[i + 1] != "\n":
                    out[i] = " "
                    i += 1
                out[i] = " "
                i += 1
            i += 1                      # the closing quote, or the newline
            continue
        if ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
            continue
        if text.startswith("//", i) or text.startswith("--", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)
