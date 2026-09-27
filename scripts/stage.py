"""Runs a single translation pipeline stage in isolation (eval harness).

Each subcommand executes one stage from :mod:`nwn_translator.pipeline.stages`,
reading its inputs from saved artifacts (``--from``) and writing its outputs
(``--out``) via :mod:`nwn_translator.pipeline.artifacts`. ``extract_dir`` persists
between stages, so the deterministic stages re-parse from it, while the LLM stages
(entities / glossary / translate) can run alone against the real API and their
outputs be inspected or hand-edited before the next stage.

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
from typing import List, Optional

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
    """Logs one progress event of the stage.

    Args:
        phase: Progress phase.
        current: Finished units.
        total: Units of the phase.
        message: File name or step description.
    """
    detail = f" {message}" if message else ""
    logger.info("[%s] %s/%s%s", phase, current, total, detail)


def _build_state(args: argparse.Namespace) -> PipelineState:
    """Creates the run settings and the pipeline state of the command line.

    Args:
        args: Command line.

    Returns:
        A state whose repacked module goes to ``--out``.
    """
    # Constructing the provider needs a non-empty key. The deterministic stages never
    # call it; the LLM stages need a real one (--api-key or NWN_TRANSLATE_API_KEY).
    input_file = Path(args.input) if args.input else Path(".")
    config = TranslationConfig(
        api_key=args.api_key or os.getenv("NWN_TRANSLATE_API_KEY") or "offline-placeholder-key",
        model=args.model,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        input_file=input_file,
        output_file=create_output_path(input_file, args.target_lang, output_dir=args.out),
        skip_cleanup=True,  # the runner keeps extract_dir between stages
        player_gender=args.player_gender,
        reasoning_effort=args.reasoning_effort,
        quiet=True,
        progress_callback=_progress if args.progress else None,
    )
    if args.max_concurrent is not None:
        config.max_concurrent_requests = max(1, args.max_concurrent)
    return PipelineState.create(config)


def _resolve_extract_dir(args: argparse.Namespace, state: PipelineState, do_extract: bool) -> None:
    """Sets ``state.extract_dir``: ``--extract-dir``, an input directory, else ``<out>/extract``.

    Args:
        args: Command line.
        state: Run state.
        do_extract: Unpack the input archive into an empty or missing directory.

    Raises:
        SystemExit: If unpacking needs an archive input, or the directory does not exist.
    """
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


def _translatable_files(args: argparse.Namespace, state: PipelineState) -> List[Path]:
    """Lists the translatable files of the extraction directory, filtered by ``--only-ext``.

    Args:
        args: Command line.
        state: Run state with an extraction directory.

    Returns:
        The files, in pipeline order.
    """
    assert state.extract_dir is not None
    files = find_translatable_files(state.extract_dir)
    if args.only_ext:
        wanted = args.only_ext if args.only_ext.startswith(".") else f".{args.only_ext}"
        files = [f for f in files if f.suffix.lower() == wanted.lower()]
    return files


def _extract(args: argparse.Namespace, state: PipelineState) -> ExtractedMap:
    """Extracts the translatable files selected by the command line.

    Args:
        args: Command line.
        state: Run state with an extraction directory.

    Returns:
        The extracted files, in pipeline order.
    """
    return stage_extract(state, _translatable_files(args, state))


def _load_world_context(state: PipelineState, art_in: Path, *, scan: bool) -> None:
    """Loads the world context and its candidates from *art_in*.

    Args:
        state: Run state; its ``world_context`` is set.
        art_in: Artifact input directory.
        scan: Scan the module when *art_in* holds no world context.
    """
    wc_path = art_in / "world_context.json"
    if wc_path.exists():
        state.world_context = artifacts.load_world_context(wc_path)
        cand_path = art_in / "candidates.json"
        if cand_path.exists():
            state.world_context.candidates = artifacts.load_candidates(cand_path)
    elif scan:
        stage_worldscan(state)


def _dump_world(state: PipelineState, art_out: Path) -> None:
    """Writes ``world_context.json`` and ``candidates.json``.

    Args:
        state: Run state; without a world context both files are written empty.
        art_out: Artifact output directory.
    """
    world = state.world_context
    artifacts.dump_world_context(art_out / "world_context.json", world)
    artifacts.dump_candidates(art_out / "candidates.json", world.candidates if world else None)


def _cmd_unpack(args: argparse.Namespace, state: PipelineState, _in: Path, out: Path) -> None:
    """Lists the translatable files of the unpacked archive in ``files.json``.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        _in: Unused.
        out: Artifact output directory (``--out``).
    """
    files = _translatable_files(args, state)
    artifacts.write_json(out / "files.json", [str(f) for f in files])
    logger.info("Unpacked to %s (%d translatable files)", state.extract_dir, len(files))
    print(str(state.extract_dir))


def _cmd_worldscan(args: argparse.Namespace, state: PipelineState, _in: Path, out: Path) -> None:
    """Scans the world into ``world_context.json`` and its name candidates, for 'entities'.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        _in: Unused.
        out: Artifact output directory (``--out``).
    """
    stage_worldscan(state)
    _dump_world(state, out)
    logger.info("Wrote %s", out / "world_context.json")


def _cmd_extract(args: argparse.Namespace, state: PipelineState, _in: Path, out: Path) -> None:
    """Extracts the translatable items into ``items.jsonl``.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        _in: Unused.
        out: Artifact output directory (``--out``).
    """
    contents = [content for _parsed, content in _extract(args, state).values()]
    artifacts.dump_items(out / "items.jsonl", contents)
    items = sum(len(content.items) for content in contents)
    logger.info("Wrote %s (%d files, %d items)", out / "items.jsonl", len(contents), items)


def _cmd_entities(args: argparse.Namespace, state: PipelineState, art_in: Path, out: Path) -> None:
    """Collects entity candidates into ``candidates.json`` (model requests).

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        art_in: Artifact input directory (``--from``).
        out: Artifact output directory (``--out``).
    """
    _load_world_context(state, art_in, scan=True)
    stage_collect_entities(state, _extract(args, state))
    _dump_world(state, out)
    logger.info("Wrote %s", out / "candidates.json")


def _cmd_glossary(args: argparse.Namespace, state: PipelineState, art_in: Path, out: Path) -> None:
    """Builds ``glossary.json`` (model requests), collecting the entities unless saved.

    Only the entities stage fills ``extracted_names``; the scan's candidates alone
    do not replace it.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        art_in: Artifact input directory (``--from``).
        out: Artifact output directory (``--out``).
    """
    _load_world_context(state, art_in, scan=True)
    if not state.world_context.extracted_names:
        stage_collect_entities(state, _extract(args, state))
    stage_build_glossary(state)
    artifacts.dump_glossary(out / "glossary.json", state.glossary)
    _dump_world(state, out)
    entries = len(state.glossary.entries) if state.glossary else 0
    logger.info("Wrote %s (%d entries)", out / "glossary.json", entries)


def _cmd_translate(args: argparse.Namespace, state: PipelineState, art_in: Path, out: Path) -> None:
    """Translates the extracted items into ``translations.json`` (model requests).

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        art_in: Artifact input directory (``--from``).
        out: Artifact output directory (``--out``).
    """
    _load_world_context(state, art_in, scan=False)
    glossary_path = art_in / "glossary.json"
    if glossary_path.exists():
        state.glossary = artifacts.load_glossary(glossary_path)
    translations = stage_translate(state, _extract(args, state))
    artifacts.dump_translations(out / "translations.json", translations)
    logger.info("Wrote %s (%d translations)", out / "translations.json", len(translations))


def _cmd_inject(args: argparse.Namespace, state: PipelineState, art_in: Path, _out: Path) -> None:
    """Patches ``translations.json`` into the unpacked files.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        art_in: Artifact input directory (``--from``).
        _out: Unused.
    """
    translations = artifacts.load_translations(art_in / "translations.json")
    stage_inject(state, _extract(args, state), translations)
    logger.info("Injected %d translations into %s", len(translations), state.extract_dir)


def _cmd_repack(_args: argparse.Namespace, state: PipelineState, _in: Path, _out: Path) -> None:
    """Packs the unpacked files into a module in ``--out``.

    Args:
        _args: Unused.
        state: Run state with an extraction directory.
        _in: Unused.
        _out: Unused.
    """
    output_path = stage_repack(state)
    logger.info("Repacked module: %s", output_path)
    print(str(output_path))


def _cmd_all(args: argparse.Namespace, state: PipelineState, _in: Path, out: Path) -> None:
    """Runs every stage in turn, writing every artifact.

    Args:
        args: Command line.
        state: Run state with an extraction directory.
        _in: Unused.
        out: Artifact output directory (``--out``).
    """
    stage_worldscan(state)
    extracted_map = _extract(args, state)
    artifacts.dump_items(out / "items.jsonl", [content for _p, content in extracted_map.values()])
    stage_collect_entities(state, extracted_map)
    _dump_world(state, out)  # with the extracted names, as 'entities' saves it
    stage_build_glossary(state)
    artifacts.dump_glossary(out / "glossary.json", state.glossary)
    translations = stage_translate(state, extracted_map)
    artifacts.dump_translations(out / "translations.json", translations)
    stage_inject(state, extracted_map, translations)
    output_path = stage_repack(state)
    logger.info("Full run complete -> %s (extract_dir=%s)", output_path, state.extract_dir)
    print(str(output_path))


#: Stage commands; each takes the command line, the pipeline state and the artifact
#: input (``--from``) and output (``--out``) directories.
COMMANDS = {
    "unpack": _cmd_unpack,
    "worldscan": _cmd_worldscan,
    "extract": _cmd_extract,
    "entities": _cmd_entities,
    "glossary": _cmd_glossary,
    "translate": _cmd_translate,
    "inject": _cmd_inject,
    "repack": _cmd_repack,
    "all": _cmd_all,
}


def _build_parser() -> argparse.ArgumentParser:
    """Builds the command-line parser.

    Returns:
        The parser of the stage commands and their options.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add = parser.add_argument
    add("command", choices=sorted(COMMANDS), help="Pipeline stage to run")
    add("input", nargs="?", default=None, help="Input .mod/.erf/.hak archive or extracted dir")
    add("--out", type=Path, default=Path("workspace/stage"), help="Artifact output dir")
    add(
        "--from", dest="art_in", type=Path, default=None, help="Artifact input dir (default: --out)"
    )
    add("--extract-dir", type=Path, default=None, help="Existing extraction directory")
    add("--only-ext", default=None, help="Restrict to one file extension, e.g. .ncs")
    add("--env-file", type=Path, default=Path(".env"))
    add("--api-key", default=None)
    add("--model", default=DEFAULT_MODEL, help=f"Model slug (default: {DEFAULT_MODEL})")
    add("--source-lang", default="auto")
    add("--target-lang", default="russian")
    # Accepted and ignored so that older command lines still parse.
    add("--temp-dir", type=Path, default=None, help="Ignored")
    add("--max-concurrent", type=int, default=None)
    add("--player-gender", choices=["male", "female"], default="male")
    add("--reasoning-effort", default=None)
    add("--progress", action="store_true", help="Log progress callbacks")
    add("--verbose", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Runs one stage command.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        The process exit code.

    Raises:
        SystemExit: If the command lacks the archive or extraction directory it needs.
    """
    args = _build_parser().parse_args(argv)
    load_dotenv(args.env_file)  # variables already set in the environment win
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    art_out = Path(args.out)
    art_out.mkdir(parents=True, exist_ok=True)
    art_in = Path(args.art_in) if args.art_in else art_out

    state = _build_state(args)
    # Repacking copies the header of the original archive.
    if args.command in ("repack", "all") and not (args.input and Path(args.input).is_file()):
        raise SystemExit(f"{args.command} requires the original archive (.mod/.erf/.hak) as input")
    _resolve_extract_dir(args, state, do_extract=args.command in ("unpack", "all"))
    COMMANDS[args.command](args, state, art_in, art_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
