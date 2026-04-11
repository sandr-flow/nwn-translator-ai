"""Per-language translation prompt examples.

Each language module exposes an ``EXAMPLES`` dict with the following keys:

- ``proper_names``: list of (english, translated) tuples for descriptive names
- ``personal_names``: list of (english, translated) tuples for character names
- ``speech_low_int``: list of (english_low, translated_good, translated_bad) for broken speech
- ``speech_low_int_pattern``: short description of the low-INT speech pattern in the target language
- ``dialog_output``: dict mapping node IDs to sample translated lines (for JSON output example)
- ``glossary_personal``: list of (english, translated) for glossary personal-name examples
- ``glossary_descriptive``: list of (english, translated_good, translated_bad) for glossary descriptive examples
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

_LANG_MODULE_MAP: Dict[str, str] = {
    "russian": "russian",
    "english": "english",
    "ukrainian": "ukrainian",
    "polish": "polish",
    "german": "german",
    "french": "french",
    "spanish": "spanish",
    "italian": "italian",
    "portuguese": "portuguese",
    "czech": "czech",
    "romanian": "romanian",
    "hungarian": "hungarian",
    "dutch": "dutch",
    "turkish": "turkish",
}

_cache: Dict[str, Dict[str, Any]] = {}


def get_examples(target_lang: str) -> Dict[str, Any]:
    """Return the examples dict for *target_lang*, falling back to English."""
    key = (target_lang or "").strip().lower()
    if key in _cache:
        return _cache[key]

    module_name = _LANG_MODULE_MAP.get(key)
    if module_name is None:
        module_name = "english"

    mod = importlib.import_module(f".{module_name}", package=__name__)
    examples: Dict[str, Any] = mod.EXAMPLES  # type: ignore[attr-defined]
    _cache[key] = examples
    return examples
