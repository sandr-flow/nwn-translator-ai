"""Reconstruct WORLD CONTEXT / GLOSSARY blocks for the example dialog and compare sizes."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from nwn_translator.context.world_context import WorldContext
from nwn_translator.glossary import Glossary
from nwn_translator.pipeline import artifacts as pipeline_artifacts

EXAMPLE = ROOT / "Almraiven full translate" / "big glossary issue example.txt"


def load_glossary(artifacts: Path) -> Glossary:
    return pipeline_artifacts.load_glossary(artifacts / "glossary.json")


def load_world_context(artifacts: Path) -> WorldContext:
    return pipeline_artifacts.load_world_context(artifacts / "world_context.json")


def extract_dialog_lines() -> list[str]:
    text = EXAMPLE.read_text(encoding="utf-8")
    return re.findall(r"<<<(.*?)>>>", text, flags=re.DOTALL)


def section_size(label: str, header_prefix: str, text: str) -> int:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(header_prefix)), None)
    if start is None:
        return 0
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].rstrip() == "" and j + 1 < len(lines) and lines[j + 1].startswith("---"):
            end = j
            break
    return sum(len(ln) + 1 for ln in lines[start:end])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ROOT / "workspace" / "almraiven_context_glossary_v2",
    )
    args = parser.parse_args()
    print(f"=== Artifacts: {args.artifacts} ===")
    glossary = load_glossary(args.artifacts)
    world = load_world_context(args.artifacts)
    lines = extract_dialog_lines()

    full_world = world.to_prompt_block(glossary=glossary, target_lang="russian")
    full_glossary = glossary.to_prompt_block()

    selected_world = world.to_prompt_block(
        glossary=glossary, target_lang="russian", source_texts=lines
    )
    selected_glossary = glossary.to_prompt_block(texts=lines)

    original = EXAMPLE.read_text(encoding="utf-8")
    original_chars = len(original)
    dialog_chars = sum(len(ln) for ln in lines)

    def stats(name: str, text: str) -> None:
        n_lines = text.count("\n") + (1 if text else 0)
        print(f"  {name:30s} chars={len(text):>7d}  lines={n_lines:>5d}")

    print("=== Source ===")
    print(f"  example file chars            = {original_chars}")
    print(f"  dialog reply chars (<<<...>>>)= {dialog_chars}  ({len(lines)} replies)")
    print()
    print("=== FULL (no source filter, what global dump emits) ===")
    stats("WORLD CONTEXT (full)", full_world)
    stats("GLOSSARY (full)", full_glossary)
    print()
    print("=== SELECTED (per-prompt, source_texts=dialog lines) ===")
    stats("WORLD CONTEXT (selected)", selected_world)
    stats("GLOSSARY (selected)", selected_glossary)
    print()

    sel_total = len(selected_world) + len(selected_glossary)
    print(f"  selected world+glossary       = {sel_total}")
    print(f"  dialog script chars           = {dialog_chars}")
    if sel_total + dialog_chars:
        share = sel_total / (sel_total + dialog_chars)
        print(f"  context share of (context+dialog) = {share:.1%}")

    print()
    print("=== Selected GLOSSARY entries ===")
    for ln in selected_glossary.splitlines()[1:]:
        print("  " + ln.strip())

    print()
    print("=== Selected WORLD CONTEXT (truncated) ===")
    for ln in selected_world.splitlines():
        print("  " + ln)


if __name__ == "__main__":
    main()
