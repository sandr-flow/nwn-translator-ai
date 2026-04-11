"""Optional diagnostic: real .git from workspace — offsets and .uti text alignment.

Skipped in CI / clean checkouts when no ``*.git`` exists under ``workspace/``.
Set ``NWN_DIAG_GIT_PATH`` to force a specific file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.nwn_translator.extractors.base import extract_local_string
from src.nwn_translator.file_handlers.gff_handler import read_gff
from src.nwn_translator.injectors.git_injector import (
    INSTANCE_LISTS,
    INSTANCE_NESTED_ITEM_LISTS,
    ITEM_INVENTORY_FIELDS,
    collect_git_strings_missing_from_translations,
)


def _find_sample_git() -> Path | None:
    env = os.environ.get("NWN_DIAG_GIT_PATH", "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None
    root = Path(__file__).resolve().parents[1]
    workspace = root / "workspace"
    if not workspace.is_dir():
        return None
    for git_path in workspace.rglob("*.git"):
        if git_path.is_file():
            return git_path
    return None


def _nested_item_entries(instance: dict, nested_key: str) -> list:
    raw = instance.get(nested_key, [])
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)]


def _assert_nested_offsets(git_path: Path, parsed: dict) -> None:
    """Every CExoLocString in walked instance lists must have a positive record offset."""
    missing: list[str] = []
    for list_key, field_names in INSTANCE_LISTS.items():
        instances = parsed.get(list_key, [])
        if not isinstance(instances, list):
            continue
        for inst_idx, instance in enumerate(instances):
            if not isinstance(instance, dict):
                continue
            ro = instance.get("_record_offsets", {})
            for fn in field_names:
                field_obj = instance.get(fn)
                if not isinstance(field_obj, dict):
                    continue
                val = field_obj.get("Value", "")
                if not val:
                    continue
                off = ro.get(fn, 0) if isinstance(ro, dict) else 0
                if off <= 0:
                    missing.append(
                        f"{git_path.name} {list_key}[{inst_idx}].{fn} value={val!r}"
                    )
            for nested_key in INSTANCE_NESTED_ITEM_LISTS.get(list_key, []):
                for j, inv in enumerate(_nested_item_entries(instance, nested_key)):
                    iro = inv.get("_record_offsets", {})
                    for fn in ITEM_INVENTORY_FIELDS:
                        field_obj = inv.get(fn)
                        if not isinstance(field_obj, dict):
                            continue
                        val = field_obj.get("Value", "")
                        if not val:
                            continue
                        off = iro.get(fn, 0) if isinstance(iro, dict) else 0
                        if off <= 0:
                            missing.append(
                                f"{git_path.name} {list_key}[{inst_idx}].{nested_key}[{j}].{fn} "
                                f"value={val!r}"
                            )
    assert not missing, "Missing _record_offsets for:\n" + "\n".join(missing[:50])


def _uti_text_for_resref(extract_root: Path, resref: str) -> str | None:
    """Return LocalizedName Value from ``resref.uti`` if present."""
    uti = extract_root / f"{resref.lower()}.uti"
    if not uti.is_file():
        uti = extract_root / f"{resref}.uti"
    if not uti.is_file():
        return None
    try:
        data = read_gff(uti, tlk=None)
    except Exception:
        return None
    loc = data.get("LocalizedName", {})
    return extract_local_string(loc) if isinstance(loc, dict) else None


def test_real_git_nested_record_offsets_and_inventory_res_alignment():
    """If a workspace ``*.git`` exists, verify patcher prerequisites and ResRef vs .uti names."""
    git_path = _find_sample_git()
    if git_path is None:
        pytest.skip("No workspace *.git found; set NWN_DIAG_GIT_PATH to enable")

    parsed = read_gff(git_path, tlk=None)
    _assert_nested_offsets(git_path, parsed)

    found = collect_git_strings_missing_from_translations(parsed, {})
    assert isinstance(found, set)

    extract_root = git_path.parent
    mismatches: list[str] = []
    for list_key in ("Creature List", "Placeable List", "StoreList"):
        instances = parsed.get(list_key, [])
        if not isinstance(instances, list):
            continue
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            for nested_key in INSTANCE_NESTED_ITEM_LISTS.get(list_key, []):
                for inv in _nested_item_entries(instance, nested_key):
                    rr = inv.get("InventoryRes") or inv.get("InventoryResRef")
                    if not rr or not isinstance(rr, str):
                        continue
                    rr = rr.strip()
                    if not rr:
                        continue
                    name_obj = inv.get("LocalizedName", {})
                    git_name = (
                        name_obj.get("Value", "")
                        if isinstance(name_obj, dict)
                        else ""
                    )
                    if not git_name:
                        continue
                    uti_name = _uti_text_for_resref(extract_root, rr)
                    if uti_name is not None and uti_name != git_name:
                        mismatches.append(
                            f"ResRef={rr!r} .git name={git_name!r} .uti name={uti_name!r}"
                        )

    if mismatches and os.environ.get("NWN_STRICT_GIT_UTI_CHECK"):
        pytest.fail(
            "Git vs .uti LocalizedName mismatch (encoding or instance override):\n"
            + "\n".join(mismatches[:50])
        )
