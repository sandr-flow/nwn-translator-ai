"""Integration tests that require a real .mod file in test_modules/.

These tests are skipped automatically if the module file is not present.
"""

import tempfile
import pytest
from pathlib import Path

from src.nwn_translator.file_handlers.erf_reader import ERFReader
from src.nwn_translator.file_handlers import read_gff
from src.nwn_translator.extractors.dialog_extractor import DialogExtractor


MODULE_PATH = Path(__file__).parent.parent / "test_modules" / "The Dark Ranger's Treasure.mod"


@pytest.fixture
def module_reader():
    """Return an ERFReader for the test module, or skip if file not found."""
    if not MODULE_PATH.exists():
        pytest.skip(f"Test module not found: {MODULE_PATH}")
    return ERFReader(MODULE_PATH)


def test_dialog_read(module_reader):
    """Extract the first .dlg from the module and verify text is produced."""
    translatable = module_reader.get_translatable_files()

    dlg_refs = [(ref, ext) for ref, ext in translatable if ext == ".dlg"]
    assert dlg_refs, "No .dlg files found in test module"

    res_ref, res_type = dlg_refs[0]

    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / f"{res_ref}.dlg"
        success = module_reader.extract_resource(res_ref, res_type, out_file)
        assert success, f"Failed to extract {res_ref}.dlg"

        parsed_data = read_gff(out_file)
        extracted = DialogExtractor().extract(out_file, parsed_data)

    assert len(extracted.items) > 0, "DialogExtractor produced 0 items"
    assert all(item.has_text() for item in extracted.items)
