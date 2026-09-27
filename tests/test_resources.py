"""The resource registry ties each extension to its loader, extractor and injector."""

from pathlib import Path

import pytest

from nwn_translator.extractors.creature_extractor import CreatureExtractor
from nwn_translator.extractors.dialog_extractor import DialogExtractor
from nwn_translator.extractors.git_extractor import GitExtractor
from nwn_translator.extractors.item_extractor import ItemExtractor
from nwn_translator.extractors.journal_extractor import JournalExtractor
from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.extractors.simple_extractors import (
    AreaExtractor,
    DoorExtractor,
    EncounterExtractor,
    ModuleExtractor,
    PlaceableExtractor,
    StoreExtractor,
    TriggerExtractor,
)
from nwn_translator.injectors.gff_injector import inject_gff
from nwn_translator.injectors.ncs_injector import inject_ncs
from nwn_translator.pipeline.stages import (
    inject_translations_into_file,
    load_parsed_and_extracted,
)
from nwn_translator.resources import (
    RESOURCE_KINDS,
    TRANSLATABLE_TYPES,
    load_gff,
    load_ncs,
)
from tests.support.ncs import action, consts, retn, write_ncs

EXPECTED_EXTRACTORS = {
    ".dlg": DialogExtractor,
    ".jrl": JournalExtractor,
    ".uti": ItemExtractor,
    ".utc": CreatureExtractor,
    ".are": AreaExtractor,
    ".utt": TriggerExtractor,
    ".utp": PlaceableExtractor,
    ".utd": DoorExtractor,
    ".ute": EncounterExtractor,
    ".utm": StoreExtractor,
    ".git": GitExtractor,
    ".ifo": ModuleExtractor,
    ".ncs": NcsExtractor,
}


def test_translatable_types_are_the_registered_extensions():
    assert TRANSLATABLE_TYPES == set(EXPECTED_EXTRACTORS)


@pytest.mark.parametrize("ext", sorted(EXPECTED_EXTRACTORS))
def test_each_extension_has_its_extractor_loader_and_injector(ext):
    kind = RESOURCE_KINDS[ext]
    assert type(kind.extractor) is EXPECTED_EXTRACTORS[ext]
    if ext == ".ncs":
        assert (kind.load, kind.inject) == (load_ncs, inject_ncs)
    else:
        assert (kind.load, kind.inject) == (load_gff, inject_gff)


def _speech_script(tmp_path: Path) -> Path:
    return write_ncs(tmp_path, "speech.ncs", consts("Hello there, friend!"), action(221, 1), retn())


def test_extension_lookup_ignores_case(tmp_path):
    path = _speech_script(tmp_path)
    loaded = load_parsed_and_extracted(path, ".NCS", None)
    assert loaded is not None
    assert [item.text for item in loaded[1].items] == ["Hello there, friend!"]


def test_unparseable_script_is_skipped(tmp_path):
    path = tmp_path / "broken.ncs"
    path.write_bytes(b"GFF V3.2")
    assert load_ncs(path, None, None) is None
    assert load_parsed_and_extracted(path, ".ncs", None) is None


def test_unregistered_extension_is_neither_extracted_nor_injected(tmp_path):
    path = _speech_script(tmp_path)
    loaded = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded is not None
    parsed, extracted = loaded
    other = path.with_suffix(".nss")
    other.write_bytes(path.read_bytes())
    assert load_parsed_and_extracted(other, ".nss", None) is None
    translations = {(other.name, item.item_id): "Changed." for item in extracted.items}
    assert inject_translations_into_file(other, parsed, extracted, translations) is None


def test_injection_reports_the_content_type(tmp_path):
    path = _speech_script(tmp_path)
    loaded = load_parsed_and_extracted(path, ".ncs", None)
    assert loaded is not None
    parsed, extracted = loaded
    translations = {item.key: "Привет, друг!" for item in extracted.items}
    result = inject_translations_into_file(
        path, parsed, extracted, translations, target_lang="russian"
    )
    assert result is not None
    assert (result.modified, result.items_updated) == (True, 1)
    assert result.metadata == {"type": "ncs_script"}
    reloaded = load_parsed_and_extracted(path, ".ncs", None, source_encoding="cp1251")
    assert reloaded is not None
    assert [item.text for item in reloaded[1].items] == ["Привет, друг!"]
