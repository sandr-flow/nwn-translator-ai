"""Run real isolated NCS translation and compare batch vs single-call routing.

This script extracts/loads only ``.ncs`` resources, runs the same
``NcsExtractor`` + ``TranslationManager`` path used by the pipeline, and writes
JSON artifacts without injecting files back into a module.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nwn_translator.ai_providers import BaseAIProvider, TranslationItem, TranslationResult
from nwn_translator.ai_providers import create_provider
from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.base import ExtractedContent
from nwn_translator.file_handlers.erf_reader import ERFReader
from nwn_translator.main import load_parsed_and_extracted
from nwn_translator.telemetry import RunMetricsRecorder
from nwn_translator.translators.translation_manager import TranslationManager

logger = logging.getLogger(__name__)


class CountingProvider(BaseAIProvider):
    """Provider wrapper that counts translate calls while delegating to a real provider."""

    def __init__(self, wrapped: BaseAIProvider) -> None:
        self.wrapped = wrapped
        self.single_calls = 0
        self.batch_calls = 0
        self.batch_items = 0
        self.batch_sizes: List[int] = []

    def get_provider_name(self) -> str:
        return self.wrapped.get_provider_name()

    def get_default_model(self) -> str:
        return self.wrapped.get_default_model()

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        self.single_calls += 1
        return self.wrapped.translate(
            text,
            source_lang,
            target_lang,
            context=context,
            glossary_block=glossary_block,
            content_profile=content_profile,
        )

    async def translate_async(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        context: Optional[str] = None,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> TranslationResult:
        self.single_calls += 1
        return await self.wrapped.translate_async(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            context=context,
            glossary_block=glossary_block,
            content_profile=content_profile,
        )

    async def translate_batch_async(
        self,
        items: List[TranslationItem],
        source_lang: str,
        target_lang: str,
        glossary_block: Optional[str] = None,
        content_profile: Optional[str] = None,
    ) -> List[TranslationResult]:
        self.batch_calls += 1
        self.batch_items += len(items)
        self.batch_sizes.append(len(items))
        return await self.wrapped.translate_batch_async(
            items=items,
            source_lang=source_lang,
            target_lang=target_lang,
            glossary_block=glossary_block,
            content_profile=content_profile,
        )

    async def classify_ncs_translate_gate_batch_async(
        self,
        entries: List[Dict[str, Any]],
        *,
        source_lang: str,
    ) -> Dict[str, Dict[str, Any]]:
        return await self.wrapped.classify_ncs_translate_gate_batch_async(
            entries,
            source_lang=source_lang,
        )

    async def close_async_client(self) -> None:
        await self.wrapped.close_async_client()


class SingleNcsTranslationManager(TranslationManager):
    """Legacy-style manager: approved NCS strings always use single-call translation."""

    @staticmethod
    def _is_ncs_batchable(item_data: dict) -> bool:
        return False


def _load_env_file(path: Optional[Path]) -> None:
    """Load simple KEY=VALUE pairs from *path* without overriding existing env."""
    if path is None or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _prepare_input(input_path: Path, keep_extract: bool) -> Path:
    """Return an extracted directory for *input_path*."""
    if input_path.is_dir():
        return input_path

    extract_dir = Path(tempfile.mkdtemp(prefix="nwn_ncs_run_"))
    logger.info("Extracting %s to %s", input_path, extract_dir)
    ERFReader(input_path, progress_callback=lambda *_args: None).extract_all(extract_dir)
    if keep_extract:
        logger.info("Keeping extracted files in %s", extract_dir)
    return extract_dir


def _iter_ncs_files(extract_dir: Path) -> List[Path]:
    """Return NCS files in stable order."""
    return sorted(
        (path for path in extract_dir.rglob("*.ncs") if path.is_file()),
        key=lambda path: str(path.relative_to(extract_dir)).lower(),
    )


def _load_ncs_contents(
    ncs_files: Iterable[Path],
    *,
    limit_files: Optional[int],
    limit_items: Optional[int],
) -> List[ExtractedContent]:
    """Parse and extract NCS content, optionally limiting file/item counts."""
    contents: List[ExtractedContent] = []
    item_count = 0
    for index, path in enumerate(ncs_files, 1):
        if limit_files is not None and len(contents) >= limit_files:
            break
        loaded = load_parsed_and_extracted(path, ".ncs", None, None)
        if loaded is None:
            continue
        _parsed, extracted = loaded
        if not extracted.items:
            continue
        if limit_items is not None:
            remaining = limit_items - item_count
            if remaining <= 0:
                break
            if len(extracted.items) > remaining:
                extracted = ExtractedContent(
                    content_type=extracted.content_type,
                    items=extracted.items[:remaining],
                    source_file=extracted.source_file,
                    metadata=extracted.metadata,
                )
        contents.append(extracted)
        item_count += len(extracted.items)
        if index % 100 == 0:
            logger.info("Scanned %d NCS files, extracted %d items", index, item_count)
    return contents


def _content_records(content: ExtractedContent) -> List[Dict[str, Any]]:
    """Return serializable source item records for one extracted content."""
    records = []
    for item in content.items:
        meta = item.metadata or {}
        records.append(
            {
                "file": Path(item.location).name if item.location else None,
                "item_id": item.item_id,
                "offset": meta.get("offset"),
                "text": item.text,
                "ncs_hint": meta.get("ncs_hint"),
                "confidence": meta.get("confidence"),
                "needs_llm_gate": meta.get("needs_llm_gate"),
            }
        )
    return records


def _combine_ncs_contents(contents: List[ExtractedContent], source_file: Path) -> ExtractedContent:
    """Combine per-file NCS extracted content into one module-level queue."""
    items = []
    for content in contents:
        items.extend(content.items)
    return ExtractedContent(
        content_type="ncs_script",
        items=items,
        source_file=source_file,
        metadata={"type": "combined_ncs", "files": len(contents)},
    )


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_config(args: argparse.Namespace, mode: str, output_dir: Path) -> TranslationConfig:
    log_path = output_dir / f"{mode}_translations.jsonl"
    return TranslationConfig(
        api_key=args.api_key or os.getenv("NWN_TRANSLATE_API_KEY", ""),
        model=args.model,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        input_file=args.input,
        translation_log=log_path,
        metrics_output=output_dir / f"{mode}_metrics.json",
        max_concurrent_requests=args.max_concurrent,
        preserve_tokens=True,
        skip_ncs_llm_gate=args.skip_ncs_llm_gate,
        reasoning_effort=args.reasoning_effort,
        quiet=True,
    )


def _run_mode(
    mode: str,
    content: ExtractedContent,
    args: argparse.Namespace,
    output_dir: Path,
) -> Dict[str, Any]:
    """Run one real NCS translation mode and write per-item results."""
    config = _build_config(args, mode, output_dir)
    config.get_api_key()
    metrics = RunMetricsRecorder()
    provider = CountingProvider(
        create_provider(
            config.api_key,
            config.model,
            reasoning_effort=config.reasoning_effort,
            player_gender=config.player_gender,
            metrics_recorder=metrics,
        )
    )
    manager_cls = SingleNcsTranslationManager if mode == "single" else TranslationManager
    manager = manager_cls(config, provider)

    started = time.monotonic()
    status = "completed"
    error: Optional[str] = None
    pending_exc: Optional[BaseException] = None
    result_path = output_dir / f"{mode}_results.jsonl"

    try:
        logger.info(
            "[%s] translating %d NCS items from %d files as one queue",
            mode,
            len(content.items),
            (content.metadata or {}).get("files", 0),
        )
        manager.translate_content(content)
    except BaseException as exc:
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        error = repr(exc)
        pending_exc = exc
        logger.warning("[%s] run stopped: %s", mode, error)
    finally:
        records: List[Dict[str, Any]] = []
        for item in content.items:
            meta = item.metadata or {}
            item_id = item.item_id or ""
            records.append(
                {
                    "file": Path(item.location).name if item.location else None,
                    "item_id": item_id,
                    "offset": meta.get("offset"),
                    "original": item.text,
                    "translated": manager.ncs_translations_by_item_id.get(item_id),
                    "translated_by_item_id": item_id in manager.ncs_translations_by_item_id,
                    "ncs_hint": meta.get("ncs_hint"),
                    "confidence": meta.get("confidence"),
                }
            )

        if config.metrics_output is not None:
            metrics.write_json(config.metrics_output)
        elapsed = time.monotonic() - started
        _write_jsonl(result_path, records)
        translated_count = sum(1 for row in records if row["translated_by_item_id"])
        stats = manager.get_statistics()
        summary = {
            "mode": mode,
            "status": status,
            "error": error,
            "elapsed_seconds": round(elapsed, 3),
            "files": (content.metadata or {}).get("files", 0),
            "items": len(content.items),
            "translated_item_ids": translated_count,
            "single_calls": provider.single_calls,
            "batch_calls": provider.batch_calls,
            "batch_items": provider.batch_items,
            "batch_sizes": provider.batch_sizes,
            "results_file": str(result_path),
            "translation_log": str(config.translation_log),
            "metrics_file": str(config.metrics_output),
            "ncs_diagnostics": stats.get("ncs_diagnostics", {}),
            "total_errors": stats.get("total_errors", 0),
        }
        (output_dir / f"{mode}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if pending_exc is not None:
        raise pending_exc
    return summary


def _load_result_map(path: Path) -> Dict[str, Dict[str, Any]]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["item_id"]] = row
    return rows


def _compare_modes(output_dir: Path) -> Dict[str, Any]:
    """Compare item-id coverage and translated text between single and batch runs."""
    single = _load_result_map(output_dir / "single_results.jsonl")
    batch = _load_result_map(output_dir / "batch_results.jsonl")
    all_ids = sorted(set(single) | set(batch))
    diffs = []
    missing_in_batch = []
    missing_in_single = []
    for item_id in all_ids:
        s = single.get(item_id)
        b = batch.get(item_id)
        if s is None:
            missing_in_single.append(item_id)
            continue
        if b is None:
            missing_in_batch.append(item_id)
            continue
        if s.get("translated") != b.get("translated"):
            diffs.append(
                {
                    "item_id": item_id,
                    "file": b.get("file") or s.get("file"),
                    "original": b.get("original") or s.get("original"),
                    "single": s.get("translated"),
                    "batch": b.get("translated"),
                }
            )

    summary = {
        "single_items": len(single),
        "batch_items": len(batch),
        "missing_in_batch": missing_in_batch,
        "missing_in_single": missing_in_single,
        "different_translations": len(diffs),
        "diffs_file": str(output_dir / "single_vs_batch_diffs.jsonl"),
    }
    _write_jsonl(output_dir / "single_vs_batch_diffs.jsonl", diffs)
    (output_dir / "comparison_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input .mod/.erf/.hak or extracted directory")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Directory for JSON artifacts"
    )
    parser.add_argument("--mode", choices=["batch", "single", "both"], default="both")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--source-lang", default="english")
    parser.add_argument("--target-lang", default="russian")
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--max-concurrent", type=int, default=4)
    parser.add_argument("--limit-files", type=int, default=None)
    parser.add_argument("--limit-items", type=int, default=None)
    parser.add_argument("--skip-ncs-llm-gate", action="store_true")
    parser.add_argument("--keep-extract", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    _load_env_file(args.env_file)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    extract_dir = _prepare_input(args.input, args.keep_extract)
    try:
        ncs_files = _iter_ncs_files(extract_dir)
        contents = _load_ncs_contents(
            ncs_files,
            limit_files=args.limit_files,
            limit_items=args.limit_items,
        )
        source_rows = [row for content in contents for row in _content_records(content)]
        _write_jsonl(output_dir / "source_items.jsonl", source_rows)
        logger.info(
            "Prepared %d NCS files with %d extracted items", len(contents), len(source_rows)
        )
        combined_content = _combine_ncs_contents(contents, extract_dir)

        modes = ["single", "batch"] if args.mode == "both" else [args.mode]
        summaries = [_run_mode(mode, combined_content, args, output_dir) for mode in modes]
        run_summary = {"input": str(args.input), "modes": summaries}
        if args.mode == "both":
            run_summary["comparison"] = _compare_modes(output_dir)
        (output_dir / "run_summary.json").write_text(
            json.dumps(run_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote NCS run artifacts to %s", output_dir)
        return 0
    finally:
        if not args.keep_extract and args.input.is_file() and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
