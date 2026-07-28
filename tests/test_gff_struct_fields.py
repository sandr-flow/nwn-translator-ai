"""Tests for direct Struct (GFF field type 14) parsing, expansion, and patching.

Per the GFF v3.2 spec, field type 14 is Struct with DataOrDataOffset holding an
index into the Struct array. These tests pin the whole contract: the parser
expands such fields into nested dicts (with ``_record_offsets`` preserved for
byte-patching), the writer emits the spec-correct type id, malformed indices
never loop, and ``.git`` extraction is unaffected by the newly visible
``AreaProperties`` subtree.
"""

import struct

from src.nwn_translator.extractors.git_extractor import GitExtractor
from src.nwn_translator.file_handlers.gff_handler import read_gff, write_gff
from src.nwn_translator.file_handlers.gff_parser import (
    GFFFile,
    GFFParser,
    GFFStruct,
    GFFType,
    GFFValue,
    _expand_struct,
)
from src.nwn_translator.file_handlers.gff_patcher import GFFPatcher


def _write_wrapper_gff(tmp_path, filename="wrapper.uti"):
    """Write a GFF whose root has a direct Struct field wrapping a locstring."""
    data = {
        "StructType": "UTI",
        "Tag": "outer_tag",
        "Wrapper": {
            "LocalizedName": {"StrRef": -1, "Value": "Ancient Blade"},
            "Charges": 3,
        },
    }
    path = tmp_path / filename
    write_gff(path, data)
    return path


class TestDirectStructFieldParsing:
    """A direct Struct field must expand into a nested dict on parse."""

    def test_expands_to_nested_dict_with_text(self, tmp_path):
        path = _write_wrapper_gff(tmp_path)
        parsed = read_gff(path)

        wrapper = parsed.get("Wrapper")
        assert isinstance(wrapper, dict), f"Wrapper not expanded: {wrapper!r}"
        assert wrapper.get("LocalizedName", {}).get("Value") == "Ancient Blade"
        assert wrapper.get("Charges") == 3

    def test_sibling_fields_unaffected(self, tmp_path):
        path = _write_wrapper_gff(tmp_path)
        parsed = read_gff(path)

        assert parsed.get("Tag") == "outer_tag"
        assert parsed.get("StructType") == "UTI"

    def test_field_type_and_record_offsets_preserved(self, tmp_path):
        path = _write_wrapper_gff(tmp_path)
        parsed = read_gff(path)

        assert parsed["_field_types"]["Wrapper"] == int(GFFType.Struct) == 14
        wrapper = parsed["Wrapper"]
        assert wrapper["_field_types"]["LocalizedName"] == int(GFFType.CExoLocString)
        # The nested locstring must stay byte-patchable: a real field record offset.
        assert wrapper["_record_offsets"]["LocalizedName"] > 0

    def test_on_disk_field_type_is_spec_14(self, tmp_path):
        """The 12-byte field record on disk must carry type id 14, not 16."""
        path = _write_wrapper_gff(tmp_path)
        gff = GFFParser(path).parse()
        raw = path.read_bytes()

        wrapper_field = gff.structs[0].fields["Wrapper"]
        assert wrapper_field.type == GFFType.Struct
        on_disk_type = struct.unpack_from("<I", raw, wrapper_field.record_offset)[0]
        assert on_disk_type == 14

    def test_writer_roundtrip_is_idempotent(self, tmp_path):
        """Two write/read cycles must not degrade the nested struct."""
        path = _write_wrapper_gff(tmp_path)
        first = read_gff(path)

        path2 = tmp_path / "wrapper2.uti"
        write_gff(path2, first)
        second = read_gff(path2)

        assert second["Wrapper"]["LocalizedName"]["Value"] == "Ancient Blade"
        assert second["Wrapper"]["Charges"] == 3
        assert second["_field_types"]["Wrapper"] == 14


class TestDirectStructFieldPatching:
    """A locstring inside a direct Struct field must be byte-patchable."""

    def test_patch_nested_locstring(self, tmp_path):
        path = _write_wrapper_gff(tmp_path)
        parsed = read_gff(path)
        offset = parsed["Wrapper"]["_record_offsets"]["LocalizedName"]

        GFFPatcher(path, text_encoding="cp1251").patch_local_string(offset, "Древний клинок")

        reread = read_gff(path)
        assert reread["Wrapper"]["LocalizedName"]["Value"] == "Древний клинок"

    def test_patch_leaves_siblings_intact(self, tmp_path):
        path = _write_wrapper_gff(tmp_path)
        parsed = read_gff(path)
        offset = parsed["Wrapper"]["_record_offsets"]["LocalizedName"]

        GFFPatcher(path, text_encoding="cp1251").patch_local_string(offset, "Древний клинок")

        reread = read_gff(path)
        assert reread["Wrapper"]["Charges"] == 3
        assert reread["Tag"] == "outer_tag"
        assert reread["StructType"] == "UTI"


class TestMalformedStructIndices:
    """Invalid struct indices must stay as ints and never loop."""

    @staticmethod
    def _gff_with_structs(struct_fields_list):
        gff = GFFFile()
        for fields in struct_fields_list:
            st = GFFStruct(struct_id=0, data_offset=0, field_count=len(fields))
            st.fields = fields
            gff.structs.append(st)
        return gff

    def test_self_reference_stays_int(self):
        gff = self._gff_with_structs([{"Self": GFFValue(GFFType.Struct, 0, record_offset=100)}])
        result = _expand_struct(gff.structs[0].fields, gff, {0})
        assert result["Self"] == 0

    def test_out_of_range_index_stays_int(self):
        gff = self._gff_with_structs([{"Broken": GFFValue(GFFType.Struct, 99, record_offset=100)}])
        result = _expand_struct(gff.structs[0].fields, gff, {0})
        assert result["Broken"] == 99

    def test_negative_index_stays_int(self):
        gff = self._gff_with_structs([{"Broken": GFFValue(GFFType.Struct, -1, record_offset=100)}])
        result = _expand_struct(gff.structs[0].fields, gff, {0})
        assert result["Broken"] == -1

    def test_two_struct_cycle_terminates(self):
        gff = self._gff_with_structs(
            [
                {"Child": GFFValue(GFFType.Struct, 1, record_offset=100)},
                {"Parent": GFFValue(GFFType.Struct, 0, record_offset=112)},
            ]
        )
        result = _expand_struct(gff.structs[0].fields, gff, {0})
        assert isinstance(result["Child"], dict)
        # The back-edge to the already-visited root stays an int.
        assert result["Child"]["Parent"] == 0

    def test_non_struct_int_field_not_expanded(self):
        """A plain DWORD that happens to equal a valid struct index stays an int."""
        gff = self._gff_with_structs(
            [
                {"HP": GFFValue(GFFType.DWORD, 1, record_offset=100)},
                {"Decoy": GFFValue(GFFType.DWORD, 7, record_offset=112)},
            ]
        )
        result = _expand_struct(gff.structs[0].fields, gff, {0})
        assert result["HP"] == 1


class TestGitAreaPropertiesRegression:
    """Expanding AreaProperties must not change what GitExtractor extracts."""

    @staticmethod
    def _git_data(with_area_properties):
        data = {
            "StructType": "GIT",
            "Creature List": [
                {
                    "FirstName": {"StrRef": -1, "Value": "Old fisherman"},
                    "LastName": {"StrRef": -1, "Value": ""},
                    "Description": {"StrRef": -1, "Value": "A weathered old man."},
                    "Race": 6,
                    "Gender": 0,
                }
            ],
        }
        if with_area_properties:
            data["AreaProperties"] = {
                "AmbientSndDay": 12,
                "AmbientSndNight": 14,
                "MusicDay": 3,
                "MusicNight": 4,
                "MusicBattle": 5,
            }
        return data

    def _extract_texts(self, tmp_path, with_area_properties, filename):
        path = tmp_path / filename
        write_gff(path, self._git_data(with_area_properties))
        parsed = read_gff(path)
        if with_area_properties:
            assert isinstance(parsed.get("AreaProperties"), dict)
        extracted = GitExtractor().extract(path, parsed)
        return sorted(item.text for item in extracted.items)

    def test_extraction_identical_with_and_without_area_properties(self, tmp_path):
        without = self._extract_texts(tmp_path, False, "plain.git")
        with_props = self._extract_texts(tmp_path, True, "withprops.git")
        assert with_props == without
        assert "A weathered old man." in with_props

    def test_no_area_properties_values_leak_into_items(self, tmp_path):
        path = tmp_path / "leak.git"
        write_gff(path, self._git_data(True))
        parsed = read_gff(path)
        extracted = GitExtractor().extract(path, parsed)
        area_keys = {"AmbientSndDay", "AmbientSndNight", "MusicDay", "MusicNight", "MusicBattle"}
        for item in extracted.items:
            assert item.text
            assert item.text not in area_keys
