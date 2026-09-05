"""Independent occurrence gold and production-stage accounting, without an API."""

import hashlib
import json
import shutil

import pytest

from scripts.evaluate_ncs_corpus import CORPUS, STAGES, evaluate, load_corpus, summarize
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes


def test_corpus_evidence_is_resolvable():
    manifest, annotations = load_corpus()
    assert (CORPUS / manifest["annotation_policy"]).is_file()
    for case in annotations["cases"].values():
        for item in case["items"]:
            for evidence in item["evidence"]:
                path = CORPUS / evidence["file"]
                if "lines" in evidence:
                    lines = path.read_text(encoding="cp1252").splitlines()
                    assert evidence["lines"]
                    assert all(1 <= line <= len(lines) for line in evidence["lines"])
                if "excerpt" in evidence:
                    excerpts = json.loads(path.read_text(encoding="utf-8"))["excerpts"]
                    excerpt = excerpts[evidence["excerpt"]]
                    raw = excerpt["text"].encode(excerpt["encoding"])
                    assert hashlib.sha256(raw).hexdigest() == excerpt["sha256"]
                    assert len(raw) == excerpt["byte_end"] - excerpt["byte_start"]


@pytest.mark.parametrize(
    "damage", ["missing_label", "wrong_text", "changed_source", "missing_risk"]
)
def test_corpus_rejects_drift(tmp_path, damage):
    root = tmp_path / "corpus"
    shutil.copytree(CORPUS, root)
    path = root / "annotations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["cases"]["adwr/check_bluff_10"]["items"]
    if damage == "missing_label":
        items.pop()
    elif damage == "wrong_text":
        items[0]["text"] = "Different text"
    elif damage == "missing_risk":
        del data["cases"]["controls/stored_value"]["items"][1]["change_risk"]
    else:
        source = root / "adwr/check_bluff_10/check_bluff_10.nss"
        source.write_bytes(source.read_bytes() + b"\n// changed\n")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AssertionError):
        load_corpus(root)


def test_summary_counts_first_rejections_and_excludes_unknown():
    rows = [
        {"label": "preserve", "change_risk": "potentially_breaking", "rejected_at": "consumer"},
        {"label": "translate", "rejected_at": "text_filter"},
        {"label": "unknown", "rejected_at": "text_filter"},
        {"label": "translate", "rejected_at": None},
        {"label": "preserve", "change_risk": "harmless", "rejected_at": None},
        {"label": "preserve", "change_risk": "unassessed", "rejected_at": "candidate"},
    ]
    result = summarize(rows)
    assert [stage["correctly_rejected"] for stage in result] == [0, 1, 0, 1, 0]
    assert [stage["incorrectly_rejected"] for stage in result] == [0, 0, 1, 0, 0]
    assert [stage["unknown_rejected"] for stage in result] == [0, 0, 1, 0, 0]
    assert [stage["cumulative_missed"] for stage in result] == [0, 0, 1, 1, 1]
    assert result[-1]["translate_remaining"] == result[-1]["preserve_remaining"] == 1
    assert result[-1]["unknown_remaining"] == 0
    assert [stage["potentially_breaking_rejected"] for stage in result] == [0, 1, 0, 0, 0]
    assert [stage["unassessed_rejected"] for stage in result] == [0, 0, 0, 1, 0]
    assert result[-1]["harmless_remaining"] == 1
    for stage in result:
        assert (
            sum(
                stage[risk + "_remaining"]
                for risk in ("potentially_breaking", "harmless", "unassessed")
            )
            == stage["preserve_remaining"]
        )
        assert (
            sum(
                stage[risk + "_rejected"]
                for risk in ("potentially_breaking", "harmless", "unassessed")
            )
            == stage["correctly_rejected"]
        )


def test_selection_trace_does_not_change_extraction():
    manifest, _ = load_corpus()
    for case in manifest["cases"]:
        path = CORPUS / case["files"]["ncs"]["path"]
        parsed = {
            "_ncs_file": parse_ncs_bytes(path.read_bytes(), source_encoding=case["encoding"]),
            "_source_encoding": case["encoding"],
        }
        plain = NcsExtractor().extract(path, parsed)
        events = []
        traced = NcsExtractor().extract(path, {**parsed, "_ncs_selection_trace": events})
        assert plain == traced
        assert events


def test_production_selection_against_independent_gold(tmp_path):
    report = evaluate(tmp_path / "first")
    assert report == evaluate(tmp_path / "second")
    assert report["modes"]["bytecode"]["stages"] == report["modes"]["matching_source"]["stages"]
    assert report["llm"] == "not_evaluated"
    assert report["scripts"] == 77
    assert report["labels"] == {"translate": 165, "preserve": 1940, "unknown": 1}
    # Harmless extras remain explicit; dangerous candidates and misses are forbidden.
    known_preserved = {
        "midnight/at_enu:c48",  # Nonlinguistic period.
        "prophet/69_client_enter:c12",
        "prophet/69_client_enter:c13",
        "prophet/69_client_enter:c14",
        "prophet/69_client_enter:c16",
        "prophet/69_client_enter:c17",  # TEST_MODE diagnostics.
        "torn/amuletofevil:c3",
        "torn/amuletofevil:c7",
        "torn/amuletofevil:c8",
        "almraiven/69_hen_spawnin:c25",  # TEST_MODE inventory diagnostics.
        "almraiven/69_hen_spawnin:c26",
        "midnight/c_end_nemudai:c40",  # Nonlinguistic comma-space.
        "prophet/69_hench_canlvl:c6",  # TEST_MODE level/XP diagnostics.
        "prophet/69_hench_canlvl:c7",
        "prophet/69_hench_canlvl:c8",
        "prophet/69_hench_canlvl:c9",
        "prophet/69_hench_canlvl:c10",
        "prophet/69_hench_canlvl:c13",
        "prophet/69_hench_canlvl:c14",
    }
    for data in report["modes"].values():
        assert data["missed"] == []
        assert set(data["preserve_at_model_boundary"]) <= known_preserved
        risk_groups = data["preserve_at_model_boundary_by_risk"]
        assert risk_groups["potentially_breaking"] == []
        assert risk_groups["unassessed"] == []
        assert set().union(*(set(ids) for ids in risk_groups.values())) == set(
            data["preserve_at_model_boundary"]
        )
        assert sum(map(len, risk_groups.values())) == len(data["preserve_at_model_boundary"])
        assert "prophet/69_hench_canlvl:c3" not in risk_groups["harmless"]
        assert "prophet/69_hench_canlvl:c6" not in risk_groups["potentially_breaking"]
        assert all(group["actual_text"] == group["text"] for group in data["groups"])
        assert [stage["stage"] for stage in data["stages"]] == list(STAGES)
        same = [
            row for row in data["occurrences"] if row["id"].startswith("controls/same_literal:")
        ]
        assert len({row["text"] for row in same}) == 1
        assert [row["label"] for row in same] == ["preserve", "translate", "preserve", "preserve"]
        assert [row["rejected_at"] is None for row in same] == [False, True, False, False]
        for breakdown in data["breakdowns"].values():
            assert sum(group["occurrences"] for group in breakdown.values()) == 2106
            for index, stage in enumerate(data["stages"]):
                for metric in stage.keys() - {"stage"}:
                    assert (
                        sum(group["stages"][index][metric] for group in breakdown.values())
                        == stage[metric]
                    )


def test_expansion_is_balanced_and_has_no_duplicate_binaries():
    manifest, annotations = load_corpus()
    hashes = [case["files"]["ncs"]["sha256"] for case in manifest["cases"]]
    assert len(hashes) == len(set(hashes))
    sampled = [case for case in manifest["cases"] if case.get("cohort") == "hash_stratified"]
    assert len(sampled) == 60
    for module in {case["module"] for case in sampled}:
        for stratum, spec in manifest["sampling"]["strata"].items():
            cases = [
                case for case in sampled if case["module"] == module and case["stratum"] == stratum
            ]
            assert len(cases) == spec["per_module"]
            assert all(spec["min"] <= case["constant_count"] <= spec["max"] for case in cases)
    # Sentence shape does not turn a dialog resref into literal speech.
    item = annotations["cases"]["torn/ta_arenafights"]["items"][28]
    assert item["text"] == "CUPRAK SMASH!!!"
    assert item["label"] == "preserve"
    assert item["change_risk"] == "potentially_breaking"
