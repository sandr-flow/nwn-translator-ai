"""Run a single translation pipeline stage in isolation (eval harness).

Each subcommand executes exactly one stage from
:mod:`nwn_translator.pipeline.stages`, reading its inputs from saved artifacts
(``--from``) and writing its outputs (``--out``) via
:mod:`nwn_translator.pipeline.artifacts`.  ``extract_dir`` is the backbone: it
persists between stages so deterministic stages re-parse from it, while the LLM
stages (entities / glossary / translate) can be run alone against the real API
and their outputs inspected or hand-edited before the next stage.

Examples::

    # Unpack once, keep the extraction directory.
    python scripts/stage.py unpack module.mod --out work

    # Build only the glossary (real API) from a saved world context.
    python scripts/stage.py glossary --extract-dir work/extract --from work --out work

    # Translate only NCS scripts (real API).
    python scripts/stage.py translate --extract-dir work/extract --from work --out work \
        --only-ext .ncs

    # Inject saved translations and repack into work/, no LLM calls. Repacking
    # needs the original archive for its header.
    python scripts/stage.py inject --extract-dir work/extract --from work
    python scripts/stage.py repack module.mod --extract-dir work/extract --out work
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

from nwn_translator.config import DEFAULT_MODEL, TranslationConfig, create_output_path
from nwn_translator.formats.erf import ERFReader
from nwn_translator.pipeline import artifacts
from nwn_translator.pipeline.stages import (
    ExtractedMap,
    PipelineState,
    find_translatable_files,
    stage_build_glossary,
    stage_collect_entities,
    stage_extract,
    stage_inject,
    stage_repack,
    stage_translate,
    stage_worldscan,
)

logger = logging.getLogger(__name__)


def _progress(phase: str, current: int, total: int, message: Optional[str]) -> None:
    """Log one progress callback of the stage."""
    detail = f" {message}" if message else ""
    logger.info("[%s] %s/%s%s", phase, current, total, detail)


# ── setup ────────────────────────────────────────────────────────────────


def _build_state(args: argparse.Namespace) -> PipelineState:
    """Create the run settings and the pipeline state from the command line."""
    # A non-empty key is required just to construct the provider.  Deterministic
    # stages (unpack/extract/inject/repack) never call it; LLM stages need a
    # real key (via --api-key or NWN_TRANSLATE_API_KEY) and fail at call time.
    api_key = args.api_key or os.getenv("NWN_TRANSLATE_API_KEY") or "offline-placeholder-key"
    input_file = Path(args.input) if args.input else Path(".")
    config_kwargs: Dict[str, Any] = {
        "api_key": api_key,
        "model": args.model,
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "input_file": input_file,
        # A repacked module goes to the artifact directory, not next to the input.
        "output_file": create_output_path(input_file, args.target_lang, output_dir=args.out),
        "skip_cleanup": True,  # runner keeps extract_dir between stages
        "player_gender": args.player_gender,
        "reasoning_effort": args.reasoning_effort,
        "quiet": True,
        "progress_callback": _progress if args.progress else None,
    }
    if args.max_concurrent is not None:
        config_kwargs["max_concurrent_requests"] = max(1, int(args.max_concurrent))
    return PipelineState.create(TranslationConfig(**config_kwargs))


def _require_archive(args: argparse.Namespace, command: str) -> None:
    """Stop unless the input is an archive file; repacking copies its header."""
    if not args.input or not Path(args.input).is_file():
        raise SystemExit(f"{command} requires the original archive (.mod/.erf/.hak) as input")


def _resolve_extract_dir(
    args: argparse.Namespace, state: PipelineState, *, do_extract: bool
) -> Path:
    """Determine (and optionally populate) the extraction directory."""
    if args.extract_dir:
        extract_dir = Path(args.extract_dir)
    elif args.input and Path(args.input).is_dir():
        extract_dir = Path(args.input)
    else:
        extract_dir = Path(args.out) / "extract"

    if do_extract and (not extract_dir.exists() or not any(extract_dir.iterdir())):
        if not args.input or Path(args.input).is_dir():
            raise SystemExit("unpack requires an archive input (.mod/.erf/.hak)")
        extract_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Extracting %s -> %s", args.input, extract_dir)
        ERFReader(Path(args.input), progress_callback=lambda *_a: None).extract_all(extract_dir)

    if not extract_dir.exists():
        raise SystemExit(f"Extraction directory not found: {extract_dir} (run 'unpack' first)")

    state.extract_dir = extract_dir
    return extract_dir


def _translatable_files(args: argparse.Namespace, state: PipelineState) -> List[Path]:
    """List translatable files under the extraction dir, filtered by ``--only-ext``."""
    assert state.extract_dir is not None
    files = find_translatable_files(state.extract_dir)
    if args.only_ext:
        wanted = args.only_ext if args.only_ext.startswith(".") else f".{args.only_ext}"
        files = [f for f in files if f.suffix.lower() == wanted.lower()]
    return files


def _maybe_load_world_context(state: PipelineState, art_in: Path) -> None:
    """Load world_context.json + candidates.json from *art_in* when present."""
    wc_path = art_in / "world_context.json"
    if wc_path.exists():
        state.world_context = artifacts.load_world_context(wc_path)
        cand_path = art_in / "candidates.json"
        if cand_path.exists():
            state.world_context.candidates = artifacts.load_candidates(cand_path)


def _build_extracted_map(args: argparse.Namespace, state: PipelineState) -> ExtractedMap:
    """Extract the translatable files selected by the command line."""
    return stage_extract(state, _translatable_files(args, state))


# ── subcommands ────────────────────────────────────────────────────────────


def cmd_unpack(args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path) -> None:
    """Unpack the archive and list its translatable files in ``files.json``."""
    extract_dir = _resolve_extract_dir(args, state, do_extract=True)
    files = _translatable_files(args, state)
    artifacts.write_json(art_out / "files.json", [str(f) for f in files])
    logger.info("Unpacked to %s (%d translatable files)", extract_dir, len(files))
    print(str(extract_dir))


def cmd_worldscan(
    args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path
) -> None:
    """Scan the world context into ``world_context.json``."""
    _resolve_extract_dir(args, state, do_extract=False)
    stage_worldscan(state)
    artifacts.dump_world_context(art_out / "world_context.json", state.world_context)
    logger.info("Wrote %s", art_out / "world_context.json")


def cmd_extract(
    args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path
) -> None:
    """Extract the translatable items into ``items.jsonl``."""
    _resolve_extract_dir(args, state, do_extract=False)
    extracted_map = _build_extracted_map(args, state)
    contents = [ec for (_pd, ec, _ext) in extracted_map.values()]
    artifacts.dump_items(art_out / "items.jsonl", contents)
    logger.info(
        "Wrote %s (%d files, %d items)",
        art_out / "items.jsonl",
        len(contents),
        sum(len(c.items) for c in contents),
    )


def cmd_entities(
    args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path
) -> None:
    """Collect entity candidates into ``candidates.json`` (model requests)."""
    _resolve_extract_dir(args, state, do_extract=False)
    _maybe_load_world_context(state, art_in)
    if state.world_context is None:
        stage_worldscan(state)
    extracted_map = _build_extracted_map(args, state)
    stage_collect_entities(state, extracted_map)
    artifacts.dump_candidates(art_out / "candidates.json", state.world_context.candidates)
    artifacts.dump_world_context(art_out / "world_context.json", state.world_context)
    logger.info("Wrote %s", art_out / "candidates.json")


def cmd_glossary(
    args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path
) -> None:
    """Curate the candidates and build ``glossary.json`` (model requests)."""
    _resolve_extract_dir(args, state, do_extract=False)
    _maybe_load_world_context(state, art_in)
    if state.world_context is None:
        stage_worldscan(state)
    if not state.world_context.candidates:
        stage_collect_entities(state, _build_extracted_map(args, state))
    stage_build_glossary(state)
    artifacts.dump_glossary(art_out / "glossary.json", state.glossary)
    artifacts.dump_candidates(art_out / "candidates.json", state.world_context.candidates)
    logger.info(
        "Wrote %s (%d entries)",
        art_out / "glossary.json",
        len(state.glossary.entries) if state.glossary else 0,
    )


def cmd_translate(
    args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path
) -> None:
    """Translate the extracted items into ``translations.json`` (model requests)."""
    _resolve_extract_dir(args, state, do_extract=False)
    _maybe_load_world_context(state, art_in)
    glossary_path = art_in / "glossary.json"
    if glossary_path.exists():
        state.glossary = artifacts.load_glossary(glossary_path)
    extracted_map = _build_extracted_map(args, state)
    translations = stage_translate(state, extracted_map)
    artifacts.dump_translations(art_out / "translations.json", translations)
    logger.info(
        "Wrote %s (%d translations)",
        art_out / "translations.json",
        len(translations),
    )


def cmd_inject(args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path) -> None:
    """Patch ``translations.json`` into the unpacked files."""
    _resolve_extract_dir(args, state, do_extract=False)
    translations = artifacts.load_translations(art_in / "translations.json")
    extracted_map = _build_extracted_map(args, state)
    stage_inject(state, extracted_map, translations)
    logger.info("Injected %d translations into %s", len(translations), state.extract_dir)


def cmd_repack(args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path) -> None:
    """Pack the unpacked files into a module in ``--out``."""
    _require_archive(args, "repack")
    _resolve_extract_dir(args, state, do_extract=False)
    output_path = stage_repack(state)
    logger.info("Repacked module: %s", output_path)
    print(str(output_path))


def cmd_all(args: argparse.Namespace, state: PipelineState, art_in: Path, art_out: Path) -> None:
    """Run the full chain stage-by-stage, dumping every artifact along the way."""
    _require_archive(args, "all")
    extract_dir = _resolve_extract_dir(args, state, do_extract=True)
    stage_worldscan(state)
    artifacts.dump_world_context(art_out / "world_context.json", state.world_context)
    extracted_map = _build_extracted_map(args, state)
    artifacts.dump_items(
        art_out / "items.jsonl", [ec for (_pd, ec, _ext) in extracted_map.values()]
    )
    stage_collect_entities(state, extracted_map)
    artifacts.dump_candidates(art_out / "candidates.json", state.world_context.candidates)
    stage_build_glossary(state)
    artifacts.dump_glossary(art_out / "glossary.json", state.glossary)
    translations = stage_translate(state, extracted_map)
    artifacts.dump_translations(art_out / "translations.json", translations)
    stage_inject(state, extracted_map, translations)
    output_path = stage_repack(state)
    logger.info("Full run complete -> %s (extract_dir=%s)", output_path, extract_dir)
    print(str(output_path))


COMMANDS = {
    "unpack": cmd_unpack,
    "worldscan": cmd_worldscan,
    "extract": cmd_extract,
    "entities": cmd_entities,
    "glossary": cmd_glossary,
    "translate": cmd_translate,
    "inject": cmd_inject,
    "repack": cmd_repack,
    "all": cmd_all,
}


def _build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=sorted(COMMANDS), help="Pipeline stage to run")
    parser.add_argument(
        "input", nargs="?", default=None, help="Input .mod/.erf/.hak archive or extracted dir"
    )
    parser.add_argument(
        "--out", type=Path, default=Path("workspace/stage"), help="Artifact output dir"
    )
    parser.add_argument(
        "--from", dest="art_in", type=Path, default=None, help="Artifact input dir (default: --out)"
    )
    parser.add_argument(
        "--extract-dir", type=Path, default=None, help="Existing extraction directory"
    )
    parser.add_argument(
        "--only-ext", default=None, help="Restrict to one file extension, e.g. .ncs"
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model slug (default: {DEFAULT_MODEL})"
    )
    parser.add_argument("--source-lang", default="auto")
    parser.add_argument("--target-lang", default="russian")
    parser.add_argument("--max-concurrent", type=int, default=None)
    parser.add_argument("--player-gender", choices=["male", "female"], default="male")
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--progress", action="store_true", help="Log progress callbacks")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run one stage command.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    args = _build_parser().parse_args(argv)
    # Variables already set in the environment win over the file.
    load_dotenv(args.env_file)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    art_out = Path(args.out)
    art_out.mkdir(parents=True, exist_ok=True)
    art_in = Path(args.art_in) if args.art_in else art_out

    state = _build_state(args)
    COMMANDS[args.command](args, state, art_in, art_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
