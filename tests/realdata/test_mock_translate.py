"""V2.4 mock-translate round-trip: full pipeline with a deterministic provider.

Runs the complete pipeline (extract -> translate -> inject -> repack) on each
corpus module using :class:`MockTranslateProvider`, then reads the output back
and checks three invariants:

1. the output ``.mod`` reads back (all resources extractable);
2. every re-extracted GFF field with translatable content carries the marker
   (player-facing content is translated in full; gaps expose patch-coverage
   bugs such as M-G1). Fields that sanitize down to tokens/punctuation only
   (``"<Deity>!"``, ``. . .``) are passthrough by design and exempt;
3. every output ``.ncs`` reparses and its preamble field ``T`` equals its size.

NCS strings are deliberately *not* held to the marker invariant: the extractor
surfaces internal/debug strings (e.g. ``"TalentHeal Enter"``) on re-extraction
that the pipeline intentionally never translates, so a universal NCS marker
check would be meaningless. The NCS integrity guarantee here is structural
(reparse + correct ``T``), which is exactly what isolates C1/C2.

``use_context=False`` keeps the world-context / glossary / contextual-dialog
subsystems out of the loop so the provider surface stays to a single
``translate`` method; dialogs flow through the same batch path as other content.
"""

from __future__ import annotations

import struct
from pathlib import Path

from nwn_translator.config import TRANSLATABLE_TYPES, TranslationConfig
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes
from nwn_translator.pipeline.stages import (
    PipelineState,
    load_parsed_and_extracted,
    run_pipeline,
)
from nwn_translator.translators.token_handler import TokenHandler
from nwn_translator.translators.translation_manager import _is_empty_after_sanitize

from ._corpus import extract_module
from ._mock_provider import MARKER, MockTranslateProvider

_NCS_EE_SIZE_OPCODE = 0x42


def _declared_ncs_size(raw: bytes) -> int | None:
    if len(raw) >= 13 and raw[8] == _NCS_EE_SIZE_OPCODE:
        return struct.unpack_from(">I", raw, 9)[0]
    return None


def test_mock_translate_roundtrip(corpus_module: Path, tmp_path: Path) -> None:
    out_path = tmp_path / "translated.mod"
    config = TranslationConfig(
        api_key="mock-key",
        input_file=corpus_module,
        output_file=out_path,
        target_lang="english",  # cp1252: lossless for english + french corpus
        use_context=False,
        temp_dir=tmp_path,
        quiet=True,
    )
    state = PipelineState(config=config, provider=MockTranslateProvider())

    result_path = run_pipeline(state)
    assert result_path.exists(), f"{corpus_module.name}: no output produced"

    # Invariant 1 + 2: re-extract the output and inspect translated fields.
    reextract_dir = extract_module(result_path, tmp_path / "reextract")

    field_total = 0
    missing_marker = 0
    ncs_total = 0
    ncs_failures: list[str] = []
    missing_examples: list[str] = []

    for path in sorted(reextract_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TRANSLATABLE_TYPES:
            continue

        if path.suffix.lower() == ".ncs":
            # NCS: structural integrity only (reparse + correct T); the marker
            # invariant does not apply (see module docstring).
            ncs_total += 1
            raw = path.read_bytes()
            try:
                parse_ncs_bytes(raw)
            except Exception as exc:  # noqa: BLE001 - robustness sweep
                ncs_failures.append(f"reparse {path.name}: {type(exc).__name__}: {exc}")
            else:
                declared = _declared_ncs_size(raw)
                if declared is not None and declared != len(raw):
                    ncs_failures.append(f"size {path.name}: T={declared} actual={len(raw)}")
            continue

        # GFF: every re-extracted field with translatable content must carry
        # the marker; token/punctuation-only fields are passthrough by design.
        loaded = load_parsed_and_extracted(path, path.suffix.lower(), None)
        if loaded is None:
            continue
        _parsed, extracted = loaded
        for item in extracted.items:
            if not item.text:
                continue
            field_total += 1
            if MARKER in item.text:
                continue
            sanitized = TokenHandler().sanitize(item.text).sanitized_text
            if _is_empty_after_sanitize(sanitized):
                continue
            missing_marker += 1
            if len(missing_examples) < 20:
                missing_examples.append(f"{path.name}: {item.text[:60]!r}")

    problems = []
    if missing_marker:
        problems.append(
            f"{missing_marker}/{field_total} re-extracted fields lack the marker, e.g.\n"
            + "\n".join(missing_examples)
        )
    if ncs_failures:
        problems.append(
            f"{len(ncs_failures)}/{ncs_total} output .ncs failed reparse/size, first 20:\n"
            + "\n".join(ncs_failures[:20])
        )

    assert not problems, f"{corpus_module.name}: mock-translate round-trip:\n" + "\n".join(problems)
