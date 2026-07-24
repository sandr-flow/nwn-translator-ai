"""Temp-dir cleanup when a pipeline stage fails mid-run."""

from pathlib import Path
from typing import List

import pytest

from nwn_translator.config import TranslationConfig
from nwn_translator.file_handlers.erf_writer import ERFWriter
from nwn_translator.pipeline import stages
from nwn_translator.pipeline.stages import PipelineState, run_pipeline


def _build_mod(path: Path) -> None:
    """Write a minimal .mod with one translatable resource."""
    writer = ERFWriter(path)
    writer.add_resource("greeting", ".dlg", b"garbage")
    writer.write()


def _leftover_temp_dirs(parent: Path) -> List[Path]:
    return [p for p in parent.iterdir() if p.name.startswith("nwn_translate_")]


def _make_state(tmp_path: Path, *, skip_cleanup: bool = False) -> PipelineState:
    input_mod = tmp_path / "in.mod"
    _build_mod(input_mod)
    temp_parent = tmp_path / "temp"
    temp_parent.mkdir()
    config = TranslationConfig(
        api_key="unused",
        input_file=input_mod,
        output_file=tmp_path / "out.mod",
        target_lang="russian",
        temp_dir=temp_parent,
        skip_cleanup=skip_cleanup,
        quiet=True,
    )
    # The provider is never reached: the test stage raises first.
    return PipelineState(config=config, provider=None)  # type: ignore[arg-type]


def _boom(_state: PipelineState) -> None:
    raise RuntimeError("stage exploded")


def test_stage_failure_still_cleans_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _make_state(tmp_path)
    monkeypatch.setattr(stages, "stage_worldscan", _boom)

    with pytest.raises(RuntimeError, match="stage exploded"):
        run_pipeline(state)

    assert _leftover_temp_dirs(tmp_path / "temp") == []
    assert state.temp_dir is None


def test_stage_failure_keeps_temp_dir_with_skip_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _make_state(tmp_path, skip_cleanup=True)
    monkeypatch.setattr(stages, "stage_worldscan", _boom)

    with pytest.raises(RuntimeError, match="stage exploded"):
        run_pipeline(state)

    assert len(_leftover_temp_dirs(tmp_path / "temp")) == 1
