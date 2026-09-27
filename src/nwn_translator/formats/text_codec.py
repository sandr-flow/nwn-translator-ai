"""Text codec for strings stored inside module resources (GFF and NCS).

Module strings are raw bytes without an encoding tag. NWN:EE displays them with
the code page chosen in the client settings, and those settings only offer the
three single-byte Windows pages in :data:`MODULE_ENCODINGS`. Reading therefore
guesses (strict UTF-8 first, then the declared page, then a fixed cascade) and
writing encodes into one of those pages.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Code pages a translated module may be written in (NWN:EE client choices).
MODULE_ENCODINGS = frozenset({"cp1250", "cp1251", "cp1252"})

# Dashes are encodable in all three pages but are replaced anyway: the game
# UI fonts lack the glyphs and show "?" instead.
_DASHES_TO_ASCII = str.maketrans({"\u2013": "-", "\u2014": "-"})

# Romanian comma-below letters are missing from cp1250; its cedilla forms
# are the conventional legacy substitute.
_ROMANIAN_COMMA_BELOW_TO_CEDILLA = str.maketrans(
    {"\u0219": "\u015f", "\u021b": "\u0163", "\u0218": "\u015e", "\u021a": "\u0162"}
)


def decode_module_text(raw: bytes, source_encoding: Optional[str] = None) -> str:
    """Decodes a module string payload (GFF CExoString/CExoLocString, NCS CONSTS).

    NWN:EE may store UTF-8, so a strict UTF-8 attempt always goes first. A
    declared *source_encoding* (from the module's source language) is tried
    next; then the fixed cascade cp1251 -> cp1252 -> latin-1. The order
    matters: cp1251 accepts almost any byte string, so without a declared
    encoding cp1252 text with diacritics decodes as Cyrillic mojibake.
    Decoding is deterministic per run, which keeps text-addressed lookups
    aligned with the on-disk bytes.

    Args:
        raw: String bytes as stored in the resource.
        source_encoding: Declared code page, or ``None`` to use the cascade.
            An unknown codec name is logged and skipped.

    Returns:
        The decoded text (``""`` for empty input).
    """
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if source_encoding:
        try:
            return raw.decode(source_encoding)
        except (UnicodeDecodeError, LookupError):
            logger.debug("Declared source encoding %s failed; falling back", source_encoding)
    for encoding in ("cp1251", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def encode_module_text(text: str, encoding: str) -> bytes:
    """Encodes translated text for a module string payload.

    Dashes become ``-`` on every page, Romanian comma-below letters become
    their cedilla forms on cp1250, and every other character the page cannot
    represent is dropped.

    Args:
        text: Text to store.
        encoding: One of :data:`MODULE_ENCODINGS`. The caller validates it.

    Returns:
        The encoded bytes.
    """
    text = text.translate(_DASHES_TO_ASCII)
    if encoding == "cp1250":
        text = text.translate(_ROMANIAN_COMMA_BELOW_TO_CEDILLA)
    return text.encode(encoding, errors="ignore")


def decode_fixed_ascii(raw: bytes) -> str:
    """Decodes a NUL-padded fixed-width ASCII name (ERF resref, GFF label).

    Args:
        raw: The fixed-width field bytes.

    Returns:
        The text before the first NUL, with non-ASCII bytes dropped.
    """
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
