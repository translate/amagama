"""Chromium's translations: .xtb files keyed by a fingerprint of the English.

The English is in .grd files (and the .grdp parts they include). Each
.xtb translation's id is grit's fingerprint of the English message with its
placeholders shown by name; this computes the same ids to join the two.
"""

import hashlib
import os
import xml.etree.ElementTree as ET

from translate.storage.base import TranslationStore, TranslationUnit


def _fingerprint(text):
    value = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:16], 16)
    return value - (1 << 64) if value & (1 << 63) else value


def message_id(text, meaning=""):
    """grit's id for a message's text (placeholders by name) and meaning."""
    value = _fingerprint(text)
    if meaning:
        value = _fingerprint(meaning) + (value << 1) + (1 if value < 0 else 0)
    return str(value & 0x7FFFFFFFFFFFFFFF)


def _parts(element):
    """The element's text and child elements in order."""
    parts = [element.text] if element.text else []
    for child in element:
        parts.append(child)
        if child.tail:
            parts.append(child.tail)
    return parts


def _trim(parts):
    """Drop surrounding whitespace and the ''' grit uses to keep it."""
    while parts and isinstance(parts[0], str) and not parts[0].strip():
        parts = parts[1:]
    if parts and isinstance(parts[0], str):
        parts = [parts[0].lstrip()] + parts[1:]
        if parts[0].startswith("'''"):
            parts[0] = parts[0][3:]
    while parts and isinstance(parts[-1], str) and not parts[-1].strip():
        parts = parts[:-1]
    if parts and isinstance(parts[-1], str):
        parts = parts[:-1] + [parts[-1].rstrip()]
        if parts[-1].endswith("'''"):
            parts[-1] = parts[-1][:-3]
    return parts


def _original(placeholder):
    """A <ph>'s content without its <ex> example."""
    return (placeholder.text or "") + "".join(
        (ET.tostring(child, encoding="unicode") if child.tag != "ex" else "") + (child.tail or "")
        for child in placeholder)


def _messages(path):
    root = ET.parse(path).getroot()
    for element in root.iter():
        if element.tag == "part" and element.get("file"):
            yield from _messages(os.path.join(os.path.dirname(path), element.get("file")))
        elif element.tag == "message" and element.get("translateable") != "false":
            yield element


def read_grd(path):
    """{id: (English text, {placeholder name: its original text})}."""
    english = {}
    for message in _messages(path):
        parts = _trim(_parts(message))
        placeholders = {part.get("name").upper(): _original(part)
                        for part in parts if not isinstance(part, str) and part.tag == "ph"}
        presentable = "".join(part if isinstance(part, str) else part.get("name").upper()
                              for part in parts).strip()
        text = "".join(part if isinstance(part, str) else placeholders.get(
            (part.get("name") or "").upper(), "") for part in parts).strip()
        english[message_id(presentable, message.get("meaning", ""))] = (text, placeholders)
    return english


def paired_store(xtb_path, grd_path):
    """English source and translated target units for one .xtb file.

    Leaves out translations with no English message, and those naming a
    placeholder the English doesn't have.
    """
    english = read_grd(grd_path)
    store = TranslationStore()
    for translation in ET.parse(xtb_path).getroot().iter("translation"):
        found = english.get(translation.get("id"))
        if found is None:
            continue
        source, placeholders = found
        target = []
        for part in _parts(translation):
            if isinstance(part, str):
                target.append(part)
            elif part.tag == "ph" and (part.get("name") or "").upper() in placeholders:
                target.append(placeholders[part.get("name").upper()])
            else:
                break
        else:
            unit = TranslationUnit(source)
            unit.target = "".join(target).strip()
            if unit.target:
                store.addunit(unit)
    return store
