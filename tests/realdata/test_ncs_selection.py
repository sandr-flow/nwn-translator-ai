"""Source-reviewed NCS selections and selective patching on the real corpus."""

import struct

import pytest

from nwn_translator.extractors.ncs_extractor import NcsExtractor
from nwn_translator.file_handlers.erf_reader import ERFReader
from nwn_translator.file_handlers.ncs_parser import parse_ncs_bytes
from nwn_translator.config import TranslationConfig
from nwn_translator.injectors.ncs_injector import NcsInjector
from nwn_translator.translators.translation_manager import TranslationManager
from ._mock_provider import MARKER, MockTranslateProvider


class ReviewedGateProvider(MockTranslateProvider):
    """Fixed reviewed labels test routing, not the accuracy of a live model."""

    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.seen = set()

    async def classify_ncs_translate_gate_batch_async(self, entries, *, source_lang):
        self.seen.update(e["text"] for e in entries)
        return {
            e["key"]: {"translate": e["text"] in self.wanted, "reason": "reviewed"} for e in entries
        }


# (resource, player text that must remain a candidate, internal text to exclude).
# These assertions exercise the bytecode fallback with no NSS files available.
CASES = {
    "A Dance with Rogues Part 1 V1.50.mod": [
        ("do_bj_knock", {"What's she in for?"}, {"BJArrested"}),
    ],
    "Almraiven.mod": [
        ("set_abelianname", {"Abelian"}, set()),
    ],
    "LES LIONS DIFFAMES_25fev2007.mod": [
        ("a_activatesaddle", {"Rapprochez-vous!"}, set()),
        ("a_addquest", set(), {"LA TOMBE DE VOTRE MERE."}),
    ],
    "Midnight.mod": [
        ("at_enu", set(), {"Light"}),
        ("c_kashia2_1", {"Good", "Evil", "Law", "Chaos"}, set()),
    ],
    "Prophet - Chapter III - That Which is Destined.mod": [
        ("dmfi_voice_exe", {"Broadcast Mode set to Local"}, {".loc", ".dm", ".say"}),
    ],
    "Torn Asunder part 1.mod": [
        ("at_pol1_attack", {"Ahh! No! Someone Help!"}, set()),
    ],
}


@pytest.mark.parametrize("with_sources", [False, True])
def test_ncs_selection_and_patch(corpus_module, tmp_path, with_sources):
    cases = CASES.get(corpus_module.name)
    if cases is None:
        pytest.skip("No reviewed NCS cases for this module")
    reader = ERFReader(corpus_module)
    all_entries = reader.read_entries()
    sources = {
        entry.res_ref: entry
        for entry in all_entries
        if reader.get_resource_type(entry.res_type) == ".nss"
    }
    entries = {
        entry.res_ref: entry
        for entry in all_entries
        if reader.get_resource_type(entry.res_type) == ".ncs"
    }
    with corpus_module.open("rb") as archive:
        for resource, wanted, forbidden in cases:
            entry = entries[resource]
            archive.seek(entry.offset)
            raw = archive.read(entry.size)
            original = parse_ncs_bytes(raw, source_encoding="cp1252")
            literals = {instr.string_value for instr in original.string_constants}
            assert wanted | forbidden <= literals, "Corpus fixture must contain the reviewed texts"
            path = tmp_path / (resource + ".ncs")
            path.write_bytes(raw)
            if with_sources and resource in sources:
                source_entry = sources[resource]
                archive.seek(source_entry.offset)
                path.with_suffix(".nss").write_bytes(archive.read(source_entry.size))
            result = NcsExtractor().extract(
                path, {"_ncs_file": original, "_source_encoding": "cp1252"}
            )
            selected = {item.text for item in result.items}
            assert wanted <= selected
            assert not forbidden & selected

            provider = ReviewedGateProvider(wanted)
            manager = TranslationManager(
                TranslationConfig(api_key="mock", input_file=path, target_lang="english"),
                provider,
            )
            manager.translate_content(result)
            assert wanted <= provider.seen, "Reviewed speech must reach the production gate"
            approved = manager.ncs_translations_by_item_id
            assert {item.text for item in result.items if item.item_id in approved} == wanted
            injection = NcsInjector().inject(
                path,
                {},
                {},
                {
                    "ncs_extracted_items": result.items,
                    "ncs_translations_by_item_id": approved,
                    "module_text_encoding": "cp1252",
                    "module_source_encoding": "cp1252",
                },
            )
            assert not injection.metadata.get("ncs_patch_failed")
            patched_raw = path.read_bytes()
            patched = parse_ncs_bytes(patched_raw, source_encoding="cp1252")
            assert len(patched.instructions) == len(original.instructions)
            expected = {
                item.metadata["offset"]: MARKER + item.text
                for item in result.items
                if item.item_id in approved
            }
            for before, after in zip(original.string_constants, patched.string_constants):
                assert after.string_value == expected.get(before.offset, before.string_value)
            before_positions = {instr.offset: i for i, instr in enumerate(original.instructions)}
            after_positions = {instr.offset: i for i, instr in enumerate(patched.instructions)}
            before_positions[len(raw)] = len(original.instructions)
            after_positions[len(patched_raw)] = len(patched.instructions)
            for before, after in zip(original.instructions, patched.instructions):
                assert (before.opcode, before.type_byte) == (after.opcode, after.type_byte)
                if before.jump_offset is not None:
                    assert (
                        before_positions[before.offset + before.jump_offset]
                        == after_positions[after.offset + after.jump_offset]
                    )
                elif not before.is_string_const:
                    assert before.args == after.args
            if raw[8] == 0x42:
                assert struct.unpack_from(">I", patched_raw, 9)[0] == len(patched_raw)
    reader.cleanup()
