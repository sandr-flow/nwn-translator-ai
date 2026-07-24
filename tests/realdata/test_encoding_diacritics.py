"""M-E realdata: declared source encoding yields mojibake-free extraction and patching.

The corpus manifest declares each module's content language. For modules whose
language maps to a Windows code page (e.g. the French cp1252 module), extraction
with the matching ``source_encoding`` must produce diacritics — never the
Cyrillic mojibake the legacy cascade generates (0xE9 ``é`` -> ``й``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nwn_translator.config import source_string_encoding
from nwn_translator.pipeline.stages import (
    inject_translations_into_file,
    load_parsed_and_extracted,
)

from ._corpus import extract_module, load_manifest

_MIN_DIACRITIC_SAMPLES = 20
_LATIN1_DIACRITICS = set("àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆßáíóúñ")


def _cyrillic_count(text: str) -> int:
    return sum(1 for ch in text if 0x0400 <= ord(ch) <= 0x04FF)


def _manifest_language(corpus_module: Path) -> str:
    manifest = load_manifest()
    for entry in manifest.get("modules", []):
        if isinstance(entry, dict) and entry.get("file") == corpus_module.name:
            return str(entry.get("language") or "")
    return ""


def _extract_all_items(extract_dir: Path, encoding: str):
    """Yield (path, parsed_data, extracted) for every translatable file."""
    from nwn_translator.config import TRANSLATABLE_TYPES

    for path in sorted(extract_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TRANSLATABLE_TYPES:
            continue
        loaded = load_parsed_and_extracted(
            path, path.suffix.lower(), None, source_encoding=encoding
        )
        if loaded is None:
            continue
        yield path, loaded[0], loaded[1]


def _require_declared_encoding(corpus_module: Path) -> str:
    language = _manifest_language(corpus_module)
    encoding = source_string_encoding(language)
    if encoding is None or language == "english":
        pytest.skip(f"{corpus_module.name}: no non-English declared language in manifest")
    return encoding


def test_extraction_has_no_mojibake(corpus_module: Path, tmp_path: Path) -> None:
    encoding = _require_declared_encoding(corpus_module)
    extract_dir = extract_module(corpus_module, tmp_path / "extract")

    diacritic_samples: list[str] = []
    mojibake: list[str] = []
    for _path, _parsed, extracted in _extract_all_items(extract_dir, encoding):
        for item in extracted.items:
            if not item.text:
                continue
            if _cyrillic_count(item.text):
                mojibake.append(item.text[:60])
            elif any(ch in _LATIN1_DIACRITICS for ch in item.text):
                diacritic_samples.append(item.text)

    assert not mojibake, (
        f"{corpus_module.name}: {len(mojibake)} extracted strings contain Cyrillic "
        f"mojibake. First 10:\n" + "\n".join(mojibake[:10])
    )
    assert len(diacritic_samples) >= _MIN_DIACRITIC_SAMPLES, (
        f"{corpus_module.name}: only {len(diacritic_samples)} diacritic-bearing strings "
        f"extracted — expected at least {_MIN_DIACRITIC_SAMPLES}"
    )


def test_patch_round_trip_preserves_diacritics(corpus_module: Path, tmp_path: Path) -> None:
    """Marker-patch diacritic strings, then re-extract and verify them byte-exactly."""
    encoding = _require_declared_encoding(corpus_module)
    # Same-code-page target keeps diacritics representable after injection.
    target_lang = {"cp1252": "french", "cp1250": "polish", "cp1251": "russian"}[encoding]
    extract_dir = extract_module(corpus_module, tmp_path / "extract")

    marker = " [MEQ]"
    patched_expectations: dict[Path, set[str]] = {}
    expected_total = 0
    for path, parsed, extracted in _extract_all_items(extract_dir, encoding):
        diacritic_items = [
            item.text
            for item in extracted.items
            if item.text and any(ch in _LATIN1_DIACRITICS for ch in item.text)
        ]
        if not diacritic_items:
            continue
        translations = {text: text + marker for text in diacritic_items}
        result = inject_translations_into_file(
            path,
            parsed,
            extracted,
            translations,
            target_lang=target_lang,
            source_encoding=encoding,
        )
        if result is None or not result.modified:
            continue
        patched_expectations[path] = {text + marker for text in diacritic_items}
        expected_total += len(patched_expectations[path])
        if expected_total >= _MIN_DIACRITIC_SAMPLES:
            break

    assert patched_expectations, f"{corpus_module.name}: no diacritic files were patched"

    verified = 0
    missing: list[str] = []
    for path, expected_texts in patched_expectations.items():
        reloaded = load_parsed_and_extracted(
            path, path.suffix.lower(), None, source_encoding=encoding
        )
        assert reloaded is not None, f"{path.name}: unreadable after patch"
        reread = {item.text for item in reloaded[1].items}
        for text in expected_texts:
            if text in reread:
                verified += 1
            else:
                missing.append(f"{path.name}: {text[:60]!r}")

    assert not missing, (
        f"{corpus_module.name}: {len(missing)} patched strings did not survive the "
        f"round trip. First 10:\n" + "\n".join(missing[:10])
    )
    assert verified >= _MIN_DIACRITIC_SAMPLES, (
        f"{corpus_module.name}: only {verified} patched diacritic strings verified — "
        f"expected at least {_MIN_DIACRITIC_SAMPLES}"
    )
