#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Filemasks select a block's files and read their language from the path.

A mask is a path relative to the block's directory, with ``*`` (within one
directory), ``**`` (any number of directories) and ``{lang}`` (the target
language, shaped like a language code: af, pt_BR, sr@latin, zh-Hans,
b+sr+Latn).
"""

import re

TOKENS = re.compile(r"(\*\*/|\*\*|\*|\{lang\})")
# BCP 47 subtags are at most 8 characters; a POSIX @modifier has no limit.
LANG_PATTERN = r"[a-z]{1,3}(?:[_+-][A-Za-z0-9]{1,8}|@[A-Za-z0-9]+)*"


def compile_mask(mask, allow_lang=True):
    """The regular expression for mask; raises ValueError if it's malformed."""
    if not mask or mask.startswith("/") or ".." in mask.split("/"):
        raise ValueError("bad filemask %r" % mask)
    if mask.count("{lang}") > 1 or (not allow_lang and "{lang}" in mask):
        raise ValueError("bad filemask %r: {lang} at most once%s"
                         % (mask, "" if allow_lang else ", and not in exclude"))
    pattern = ""
    wildcard = 0
    # Wildcards capture w0, w1, ... so a template can reuse what they matched.
    for token in TOKENS.split(mask):
        if token in ("**/", "**", "*"):
            pattern += "(?P<w%d>%s)" % (wildcard, {"**/": "(?:.*/)?", "**": ".*",
                                                    "*": "[^/]*?"}[token])
            wildcard += 1
        elif token == "{lang}":
            pattern += "(?P<lang>%s)" % LANG_PATTERN
        else:
            pattern += re.escape(token)
    return re.compile(pattern + r"\Z")


def wildcards(mask):
    """The wildcard tokens of mask, in order."""
    return [token for token in TOKENS.split(mask) if token in ("**/", "**", "*")]


def fill(template, match):
    """template with each wildcard replaced by what the same one in match took."""
    parts = []
    wildcard = 0
    for token in TOKENS.split(template):
        if token in ("**/", "**", "*"):
            parts.append(match.group("w%d" % wildcard))
            wildcard += 1
        else:
            parts.append(token)
    return "".join(parts)


class Selection(object):
    """The files a block imports: those matching a mask and no exclude."""

    def __init__(self, masks=None, excludes=None):
        self.mask_texts = list(masks or [])
        self.masks = [compile_mask(mask) for mask in self.mask_texts]
        self.excludes = [compile_mask(mask, allow_lang=False) for mask in excludes or []]

    @property
    def gives_language(self):
        return bool(self.masks) and all("lang" in mask.groupindex for mask in self.masks)

    def select(self, path, lang):
        """The file's language if it's selected, else None."""
        path = path.replace("\\", "/")
        if path.endswith(".pot") or any(exclude.match(path) for exclude in self.excludes):
            return None
        if not self.masks:
            return lang
        for mask in self.masks:
            match = mask.match(path)
            if match:
                return match.groupdict().get("lang") or lang
        return None
