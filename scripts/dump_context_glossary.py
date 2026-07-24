"""Run the world-context and glossary stage, then stop before translation.

This diagnostic script executes the same pre-translation work used by the full
module pipeline:

1. unpack the input archive;
2. scan static world context;
3. extract translatable content;
4. collect entity candidates from resources and item text;
5. run LLM entity extraction, glossary curation, and glossary translation;
6. write JSON/text artifacts without translating or injecting module content.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nwn_translator.config import TranslationConfig
from nwn_translator.context.entity_candidates import EntityCandidateRegistry
from nwn_translator.context.entity_extractor import EntityExtractor
from nwn_translator.context.world_context import WorldContext, WorldScanner
from nwn_translator.extractors.base import ExtractedContent
from nwn_translator.glossary import Glossary, GlossaryBuilder
from nwn_translator.glossary_curator import GlossaryCurator
from nwn_translator.main import ModuleTranslator
from nwn_translator.pipeline.artifacts import candidate_to_dict, world_context_to_dict

logger = logging.getLogger(__name__)


CONTEXT_GLOSSARY_DEFAULT_MODEL = "google/gemini-3-flash-preview"

ExtractedMap = Dict[Path, Tuple[Dict[str, Any], ExtractedContent, str]]


def _load_env_file(path: Optional[Path]) -> None:
    """Load simple KEY=VALUE pairs from *path* without overriding the environment."""
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


def _progress(phase: str, current: int, total: int, message: Optional[str]) -> None:
    """Log progress callback events from pipeline helpers."""
    detail = f" {message}" if message else ""
    logger.info("[%s] %s/%s%s", phase, current, total, detail)


def _extract_all(translator: ModuleTranslator, translatable_files: list[Path]) -> ExtractedMap:
    """Run Phase A extraction exactly up to the extracted-content map."""
    extracted_map: ExtractedMap = {}
    total_files = len(translatable_files)
    max_workers = max(1, int(getattr(translator.config, "max_concurrent_requests", 4)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(translator._extract_file, file_path): file_path
            for file_path in translatable_files
        }
        completed_count = 0
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            completed_count += 1
            if translator.config.progress_callback is not None:
                translator.config.progress_callback(
                    "extracting_content",
                    completed_count,
                    total_files,
                    file_path.name,
                )
            translator._check_cancel()
            try:
                result = future.result()
                if result is not None:
                    parsed_data, extracted, file_ext = result
                    extracted_map[file_path] = (parsed_data, extracted, file_ext)
            except Exception as exc:
                error_msg = f"Error extracting {file_path.name}: {exc}"
                with translator._stats_lock:
                    translator.stats["errors"].append(error_msg)
                logger.error(error_msg)
    return extracted_map


def _collect_entity_candidates(
    translator: ModuleTranslator,
    extracted_map: ExtractedMap,
) -> None:
    """Populate world_context.candidates from extracted content and LLM extraction."""
    if translator.world_context is None or not extracted_map:
        return

    if translator.config.progress_callback:
        translator.config.progress_callback("scanning", 0, 1, "Extracting entities from text...")

    all_items = []
    extracted_contents = []
    for _path, (_parsed_data, extracted, _file_ext) in extracted_map.items():
        all_items.extend(extracted.items)
        extracted_contents.append(extracted)

    known_names = {name for name, _category in translator.world_context.get_all_names()}
    extracted_registry = EntityCandidateRegistry.from_extracted_content(extracted_contents)
    translator.world_context.candidates.extend(extracted_registry.values())

    llm_registry = EntityExtractor().extract_candidates(
        all_items,
        translator.provider,
        translator.config,
        known_names,
        progress_callback=translator.config.progress_callback,
    )
    translator.world_context.candidates.extend(llm_registry.values())
    translator.world_context.extracted_names = llm_registry.glossary_pairs()

    if translator.world_context.candidates:
        translator.metrics_recorder.increment(
            "entity_candidates.raw",
            len(translator.world_context.candidates.values()),
        )


def _build_glossary(translator: ModuleTranslator) -> None:
    """Run LLM glossary curation and glossary translation."""
    if translator.world_context is None:
        translator.glossary = Glossary()
        return

    if translator.config.progress_callback:
        translator.config.progress_callback("scanning", 0, 1, "Building glossary...")

    GlossaryCurator().curate(
        translator.world_context.candidates,
        translator.provider,
        translator.config,
        progress_callback=translator.config.progress_callback,
    )

    curated_counts: Dict[str, int] = {}
    for candidate in translator.world_context.candidates.values():
        key = f"entity_candidates.{candidate.curation_decision}"
        curated_counts[key] = curated_counts.get(key, 0) + 1
    for key, value in curated_counts.items():
        translator.metrics_recorder.increment(key, value)

    translator.glossary = GlossaryBuilder().build(
        translator.world_context,
        translator.provider,
        translator.config,
        progress_callback=translator.config.progress_callback,
    )

    if translator.config.progress_callback:
        translator.config.progress_callback("scanning", 1, 1, "done")


def run_context_glossary_stage(config: TranslationConfig) -> tuple[ModuleTranslator, ExtractedMap]:
    """Run the context/glossary stage and return the translator plus extracted map."""
    translator = ModuleTranslator(config)

    logger.info("Extracting module: %s", config.input_file)
    extract_dir = translator._extract_module()
    if config.progress_callback:
        config.progress_callback("extracting", 1, 1, "done")
    translator._check_cancel()

    logger.info("Finding translatable files...")
    translatable_files = translator._find_translatable_files(extract_dir)
    logger.info("Found %d translatable files", len(translatable_files))

    translator._gff_cache = {}

    logger.info("Building world context...")
    scanner = WorldScanner()
    translator.world_context = scanner.scan_directory(
        extract_dir,
        gff_cache=translator._gff_cache,
        progress_callback=config.progress_callback,
    )

    logger.info("Extracting translatable content...")
    extracted_map = _extract_all(translator, translatable_files)
    logger.info("Extracted content from %d files", len(extracted_map))

    _collect_entity_candidates(translator, extracted_map)
    logger.info(
        "Collected %d entity candidates",
        len(translator.world_context.candidates.values()) if translator.world_context else 0,
    )

    _build_glossary(translator)
    logger.info("Built glossary with %d entries", len(translator.glossary.entries or {}))

    return translator, extracted_map


def _decision_counts(world_context: Optional[WorldContext]) -> Dict[str, int]:
    """Return counts by curation decision."""
    if world_context is None:
        return {}
    counts: Dict[str, int] = {}
    for candidate in world_context.candidates.values():
        decision = candidate.curation_decision or "unknown"
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def write_artifacts(
    output_dir: Path,
    translator: ModuleTranslator,
    extracted_map: ExtractedMap,
) -> Dict[str, Any]:
    """Write dry-run artifacts and return the summary document."""
    output_dir.mkdir(parents=True, exist_ok=True)

    world_context = translator.world_context
    glossary = translator.glossary or Glossary()
    candidates = world_context.candidates.values() if world_context is not None else []

    dialog_files = [path for path, (_pd, _ex, ext) in extracted_map.items() if ext == ".dlg"]
    item_count = sum(len(extracted.items) for _pd, extracted, _ext in extracted_map.values())
    non_dialog_item_count = sum(
        len(extracted.items)
        for path, (_pd, extracted, _ext) in extracted_map.items()
        if path not in dialog_files
    )

    artifacts = {
        "summary": output_dir / "summary.json",
        "candidates": output_dir / "candidates.json",
        "world_context_json": output_dir / "world_context.json",
        "world_context_text": output_dir / "world_context.txt",
        "glossary_json": output_dir / "glossary.json",
        "glossary_text": output_dir / "glossary.txt",
        "metrics": output_dir / "metrics.json",
    }

    world_context_text = ""
    if world_context is not None:
        world_context_text = world_context.to_prompt_block(
            glossary=glossary,
            target_lang=translator.config.target_lang,
        )

    summary: Dict[str, Any] = {
        "input_file": str(translator.config.input_file),
        "target_lang": translator.config.target_lang,
        "source_lang": translator.config.source_lang,
        "provider": translator.provider.get_provider_name(),
        "model": translator.config.model,
        "translatable_files": (
            len(translator._find_translatable_files(translator.extract_dir))
            if translator.extract_dir
            else 0
        ),
        "extracted_files": len(extracted_map),
        "dialog_files": len(dialog_files),
        "items": item_count,
        "non_dialog_items": non_dialog_item_count,
        "entity_candidates": len(candidates),
        "entity_candidate_decisions": _decision_counts(world_context),
        "glossary_entries": len(glossary.entries),
        "metrics": translator.metrics_recorder.summary(),
        "artifacts": {key: str(path) for key, path in artifacts.items()},
    }

    artifacts["candidates"].write_text(
        json.dumps([candidate_to_dict(c) for c in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    artifacts["world_context_json"].write_text(
        json.dumps(world_context_to_dict(world_context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    artifacts["world_context_text"].write_text(world_context_text, encoding="utf-8")
    artifacts["glossary_json"].write_text(
        json.dumps(glossary.entries, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    artifacts["glossary_text"].write_text(glossary.to_prompt_block(), encoding="utf-8")
    translator.metrics_recorder.write_json(artifacts["metrics"])
    artifacts["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _default_output_dir(input_file: Path) -> Path:
    """Return the default artifact directory for *input_file*."""
    return Path("workspace") / f"context_glossary_{input_file.stem}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path, help="Input .mod/.erf/.hak archive")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Artifact directory (default: workspace/context_glossary_<module-stem>)",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--model",
        default=CONTEXT_GLOSSARY_DEFAULT_MODEL,
        help=f"OpenRouter model slug (default: {CONTEXT_GLOSSARY_DEFAULT_MODEL})",
    )
    parser.add_argument("--source-lang", default="auto")
    parser.add_argument("--target-lang", default="russian")
    parser.add_argument("--temp-dir", type=Path, default=Path("./temp_nwn_translate"))
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--max-concurrent", type=int, default=None)
    parser.add_argument("--player-gender", choices=["male", "female"], default="male")
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--progress", action="store_true", help="Log pipeline progress callbacks")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _load_env_file(args.env_file)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    input_file = args.input_file.resolve()
    if not input_file.exists():
        raise FileNotFoundError(f"Input archive not found: {input_file}")

    output_dir = (args.out or _default_output_dir(input_file)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config_kwargs: Dict[str, Any] = {
        "api_key": args.api_key or os.getenv("NWN_TRANSLATE_API_KEY", ""),
        "model": args.model,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "input_file": input_file,
        "temp_dir": args.temp_dir,
        "skip_cleanup": bool(args.keep_temp),
        "player_gender": args.player_gender,
        "reasoning_effort": args.reasoning_effort,
        "metrics_output": output_dir / "metrics.json",
        "quiet": True,
        "progress_callback": _progress if args.progress else None,
    }
    if args.max_concurrent is not None:
        config_kwargs["max_concurrent_requests"] = max(1, int(args.max_concurrent))

    config = TranslationConfig(**config_kwargs)

    translator: Optional[ModuleTranslator] = None
    try:
        translator, extracted_map = run_context_glossary_stage(config)
        summary = write_artifacts(output_dir, translator, extracted_map)
    finally:
        if translator is not None and not config.skip_cleanup:
            translator._cleanup()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nArtifacts written to: {output_dir}")


if __name__ == "__main__":
    main()
