"""Measure deterministic NCS selection against independently reviewed occurrences.

No API calls, translations or injections are performed. The real manager runs
its pre-gate checks; a provider records exactly which occurrences reach the
model boundary. Those candidates are not reported as model-approved strings.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nwn_translator.ai_providers.base import BaseAIProvider
from nwn_translator.config import TranslationConfig
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.file_handlers.ncs_concat import find_concat_chains, merged_text
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes
from nwn_translator.translators.translation_manager import TranslationManager

CORPUS = ROOT / "tests/fixtures/ncs_selection"
STAGES = ("units", "consumer", "text_filter", "candidate", "pre_gate")
CHANGE_RISKS = ("potentially_breaking", "harmless", "unassessed")


class BoundaryProvider(BaseAIProvider):
    """Observe the model input, without substituting gold labels for its output."""

    def __init__(self):
        super().__init__(api_key="offline", model="offline")
        self.offsets = set()

    def get_default_model(self):
        return "offline"

    def get_provider_name(self):
        return "offline"

    def translate(self, *args, **kwargs):
        raise AssertionError("Corpus selection evaluation must not translate")

    async def classify_ncs_translate_gate_batch_async(self, entries, *, source_lang):
        self.offsets.update(entry["offset"] for entry in entries)
        return {entry["key"]: {"translate": False, "reason": "not_evaluated"} for entry in entries}


def load_corpus(root: Path = CORPUS):
    """Reject changed files or incomplete labels instead of quietly scoring them."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    annotations = json.loads((root / "annotations.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == annotations["schema_version"] == 2
    ids = [case["id"] for case in manifest["cases"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(annotations["cases"])
    files = list(manifest["references"])
    for case in manifest["cases"]:
        files.extend(case["files"].values())
        raw = (root / case["files"]["ncs"]["path"]).read_bytes()
        constants = parse_ncs_bytes(raw, source_encoding=case["encoding"]).string_constants
        gold = annotations["cases"][case["id"]]
        assert len(constants) == case["constant_count"] == len(gold["items"]), case["id"]
        for index, (constant, item) in enumerate(zip(constants, gold["items"])):
            assert (index, constant.offset, constant.string_value) == (
                item["const_index"],
                item["offset"],
                item["text"],
            ), (case["id"], index)
            assert item["label"] in {"translate", "preserve", "unknown"}
            assert item["role"] and item["rationale"] and item["evidence"]
            if item["label"] == "preserve":
                assert item.get("change_risk") in CHANGE_RISKS, (case["id"], index)
                assert item.get("risk_rationale"), (case["id"], index)
            for evidence in item["evidence"]:
                assert (root / evidence["file"]).is_file(), evidence
        grouped = set()
        for group in gold["groups"]:
            members = set(group["const_indices"])
            assert len(members) == len(group["const_indices"]) >= 2
            assert members <= set(range(len(constants))) and not members & grouped
            assert all(gold["items"][i]["label"] == "translate" for i in members)
            grouped.update(members)
    for file in files:
        assert (
            hashlib.sha256((root / file["path"]).read_bytes()).hexdigest() == file["sha256"]
        ), file
    return manifest, annotations


def evaluate_case(case, gold, work: Path, with_sources: bool, root: Path = CORPUS):
    work.mkdir(parents=True, exist_ok=True)
    path = work / (case["resource"] + ".ncs")
    shutil.copyfile(root / case["files"]["ncs"]["path"], path)
    source = case["files"].get("nss")
    if with_sources and source:
        shutil.copyfile(root / source["path"], path.with_suffix(".nss"))
    ncs = parse_ncs_bytes(path.read_bytes(), source_encoding=case["encoding"])
    events = []
    content = NcsExtractor().extract(
        path,
        {
            "_ncs_file": ncs,
            "_source_encoding": case["encoding"],
            "_ncs_selection_trace": events,
        },
    )
    provider = BoundaryProvider()
    manager = TranslationManager(TranslationConfig(api_key="offline", input_file=path), provider)
    manager._run_ncs_llm_gate([{"item": item} for item in content.items])
    index_by_offset = {item["offset"]: item["const_index"] for item in gold["items"]}
    for item in content.items:
        parts = item.metadata.get("concat_parts") or [{"offset": item.metadata["offset"]}]
        events.append(
            {
                "stage": "pre_gate",
                "kept": item.metadata["offset"] in provider.offsets,
                "reason": (
                    "model_boundary" if item.metadata["offset"] in provider.offsets else "hard_veto"
                ),
                "const_indices": [
                    index_by_offset[part["offset"]] for part in parts if "offset" in part
                ],
            }
        )
    # Every occurrence must have one decision at each stage it actually reaches.
    alive = set(range(len(gold["items"])))
    rows = [
        {"id": case["id"] + ":c" + str(i), **item, "rejected_at": None, "rejection_reason": None}
        for i, item in enumerate(gold["items"])
    ]
    for stage in STAGES:
        stage_events = [event for event in events if event["stage"] == stage]
        observed = [i for event in stage_events for i in event["const_indices"]]
        assert len(observed) == len(set(observed)) and set(observed) == alive, (case["id"], stage)
        for event in stage_events:
            if not event["kept"]:
                for i in event["const_indices"]:
                    rows[i]["rejected_at"] = stage
                    rows[i]["rejection_reason"] = event["reason"]
                    alive.remove(i)
    chains = find_concat_chains(ncs)
    actual_groups = {
        tuple(index_by_offset[lit.offset] for lit in chain.lits()): merged_text(chain)
        for chain in chains.values()
    }
    groups = [
        {
            **group,
            "case": case["id"],
            "actual_text": actual_groups.get(tuple(group["const_indices"])),
        }
        for group in gold["groups"]
    ]
    return rows, groups


def summarize(rows):
    result = []
    alive = list(rows)
    lost = 0
    for stage in STAGES:
        removed = [row for row in alive if row["rejected_at"] == stage]
        alive = [row for row in alive if row["rejected_at"] != stage]
        labels = Counter(row["label"] for row in removed)
        remaining = Counter(row["label"] for row in alive)
        removed_risks = Counter(row["change_risk"] for row in removed if row["label"] == "preserve")
        remaining_risks = Counter(row["change_risk"] for row in alive if row["label"] == "preserve")
        lost += labels["translate"]
        result.append(
            {
                "stage": stage,
                "correctly_rejected": labels["preserve"],
                "incorrectly_rejected": labels["translate"],
                "unknown_rejected": labels["unknown"],
                "translate_remaining": remaining["translate"],
                "preserve_remaining": remaining["preserve"],
                "unknown_remaining": remaining["unknown"],
                "cumulative_missed": lost,
                **{risk + "_rejected": removed_risks[risk] for risk in CHANGE_RISKS},
                **{risk + "_remaining": remaining_risks[risk] for risk in CHANGE_RISKS},
            }
        )
    return result


def evaluate(work: Path, root: Path = CORPUS):
    manifest, annotations = load_corpus(root)
    report = {
        "labels": dict(
            Counter(
                item["label"] for gold in annotations["cases"].values() for item in gold["items"]
            )
        ),
        "scripts": len(manifest["cases"]),
        "llm": "not_evaluated",
        "modes": {},
    }
    for with_sources, mode in [(False, "bytecode"), (True, "matching_source")]:
        rows, groups = [], []
        for case in manifest["cases"]:
            case_rows, case_groups = evaluate_case(
                case, annotations["cases"][case["id"]], work / mode / case["id"], with_sources, root
            )
            for row in case_rows:
                row["module"] = case["id"].split("/")[0]
                row["cohort"] = case.get(
                    "cohort", "targeted" if case["kind"] == "real" else "controls"
                )
            rows.extend(case_rows)
            groups.extend(case_groups)
        breakdowns = {}
        for dimension in ("module", "cohort"):
            breakdowns[dimension] = {}
            for value in sorted({row[dimension] for row in rows}):
                subset = [row for row in rows if row[dimension] == value]
                breakdowns[dimension][value] = {
                    "scripts": len({row["id"].rsplit(":c", 1)[0] for row in subset}),
                    "occurrences": len(subset),
                    "labels": dict(Counter(row["label"] for row in subset)),
                    "unique_texts_by_label": {
                        label: len({row["text"] for row in subset if row["label"] == label})
                        for label in ("translate", "preserve", "unknown")
                    },
                    "stages": summarize(subset),
                }
        report["modes"][mode] = {
            "stages": summarize(rows),
            "breakdowns": breakdowns,
            "occurrences": rows,
            "groups": groups,
            "missed": [
                row["id"] for row in rows if row["label"] == "translate" and row["rejected_at"]
            ],
            "preserve_at_model_boundary": [
                row["id"]
                for row in rows
                if row["label"] == "preserve" and row["rejected_at"] is None
            ],
            # This is exposure at the model boundary, not measured injected changes.
            "preserve_at_model_boundary_by_risk": {
                risk: [
                    row["id"]
                    for row in rows
                    if row.get("change_risk") == risk and row["rejected_at"] is None
                ]
                for risk in CHANGE_RISKS
            },
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/local/ncs-corpus-report.json")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ncs-eval-", dir=args.out.parent) as work:
        report = evaluate(Path(work))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for mode, data in report["modes"].items():
        print(mode)
        for stage in data["stages"]:
            print(json.dumps(stage))
    print("Report:", args.out)


if __name__ == "__main__":
    main()
