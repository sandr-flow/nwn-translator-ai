"""Pipeline behaviour when the input archive has no translatable resources.

The run must still produce a valid output archive (a byte-identical copy of
the input) instead of returning a path that was never written.
"""

from pathlib import Path

from nwn_translator.config import TranslationConfig
from nwn_translator.file_handlers.erf_reader import ERFReader
from nwn_translator.file_handlers.erf_writer import ERFWriter
from nwn_translator.pipeline.stages import PipelineState, run_pipeline


def _build_mod_without_translatables(path: Path) -> None:
    """Write a minimal .mod holding only a non-translatable resource (.nss)."""
    writer = ERFWriter(path)
    writer.add_resource("cleanup", ".nss", b"void main() {}")
    writer.write()


def test_no_translatable_files_outputs_copy_of_input(tmp_path: Path) -> None:
    input_mod = tmp_path / "empty.mod"
    _build_mod_without_translatables(input_mod)
    out_path = tmp_path / "out.mod"
    config = TranslationConfig(
        api_key="unused",
        input_file=input_mod,
        output_file=out_path,
        target_lang="russian",
        temp_dir=tmp_path / "temp",
        quiet=True,
    )
    # The provider is never touched on this path.
    state = PipelineState(config=config, provider=None)  # type: ignore[arg-type]

    result = run_pipeline(state)

    assert result == out_path
    assert out_path.exists(), "no output archive produced"
    assert out_path.read_bytes() == input_mod.read_bytes(), "output is not a copy of the input"

    reader = ERFReader(out_path)
    reader.read_header()
    entries = reader.read_entries()
    assert [e.res_ref for e in entries] == ["cleanup"]
