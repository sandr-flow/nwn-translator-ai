"""Tests for prompt context budget selection."""

from src.nwn_translator.context.world_context import NPCInfo, WorldContext
from src.nwn_translator.glossary import Glossary


def test_world_context_budget_does_not_pull_every_generic_match():
    context = WorldContext()
    for idx in range(80):
        context.npcs[f"hf_{idx}"] = NPCInfo(
            tag=f"hf_{idx}",
            first_name="Human",
            last_name="Female",
            description="Generic citizen.",
            race="Human",
            gender="Female",
            conversation="",
        )
    context.npcs["gewia"] = NPCInfo(
        tag="gewia",
        first_name="Gewia",
        last_name="the Wererat",
        description="Bolts from the Diving Dolphin.",
        race="Wererat",
        gender="Female",
        conversation="gewia",
    )
    context.areas["dolphin"] = "Diving Dolphin"

    block = context.to_prompt_block(
        source_texts=["Gewia the Wererat speaks in the Diving Dolphin."],
    )

    assert "Gewia the Wererat" in block
    assert "Diving Dolphin" in block
    assert block.count("Human Female") == 0


def test_glossary_prompt_budget_caps_relevant_prefix_explosion():
    entries = {f"Almraiven - Area {idx}": f"Алмрейвен {idx}" for idx in range(100)}
    entries["Gewia the Wererat"] = "Гевия-веркрыса"
    glossary = Glossary(entries=entries)

    block = glossary.to_prompt_block(texts=["Gewia the Wererat mentions Almraiven only briefly."])

    assert '"Gewia the Wererat"' in block
    assert block.count("Almraiven - Area") <= 40
    assert len(block) < 6000


def test_world_context_drops_generic_npcs_pulled_via_tag_match():
    """Generic NPCs whose name is shared (e.g. ``Human Female``) must not be
    selected just because their tag fragment matches a token in the dialog."""
    context = WorldContext()
    context.npcs["AUREN_CONTACT_NPC1"] = NPCInfo(
        tag="AUREN_CONTACT_NPC1",
        first_name="Human",
        last_name="Female",
        description="Generic citizen.",
        race="Human",
        gender="Female",
        conversation="",
    )
    context.npcs["GEWIA"] = NPCInfo(
        tag="GEWIA",
        first_name="Gewia",
        last_name="the Wererat",
        description="A wererat in disguise.",
        race="Wererat",
        gender="Female",
        conversation="gewia",
    )

    block = context.to_prompt_block(
        source_texts=["Gewia mentions the Auren Society."],
    )

    assert "Gewia the Wererat" in block
    # Tag fragment ``AUREN`` matches ``Auren Society`` in the dialog, but the
    # NPC's name ``Human Female`` is not in the dialog, so the generic must
    # not be admitted via the tag-fragment evidence path alone.
    assert "[AUREN_CONTACT_NPC1]" not in block


def test_world_context_drops_generic_npcs_when_label_appears_descriptively():
    """A descriptive mention like ``this particular Human Female`` must not
    pull in every NPC sharing that generic label — the label is shared and
    indistinguishable, so admitting all carriers floods the prompt with noise.
    """
    context = WorldContext()
    for idx in range(20):
        context.npcs[f"NPC_{idx}"] = NPCInfo(
            tag=f"NPC_{idx}",
            first_name="Human",
            last_name="Female",
            description=f"Generic NPC {idx}.",
            race="Human",
            gender="Female",
            conversation="",
        )
    context.npcs["GEWIA"] = NPCInfo(
        tag="GEWIA",
        first_name="Gewia",
        last_name="the Wererat",
        description="A wererat in disguise.",
        race="Wererat",
        gender="Female",
        conversation="gewia",
    )

    block = context.to_prompt_block(
        source_texts=[
            "There's something strange about this particular Human Female, "
            "for Gewia the Wererat is hiding among them."
        ],
    )

    assert "Gewia the Wererat" in block
    assert "Human Female" not in block


def test_glossary_drops_compound_entries_without_significant_match():
    """Hierarchical glossary keys must require all non-common components to
    match the source, not just the shared prefix."""
    entries = {
        f"Almraiven - District {idx} - Street {idx}": f"Алмрайвен {idx}" for idx in range(20)
    }
    entries["Almraiven"] = "Алмрайвен"
    entries["Almraiven - Dock Ward - Rosetyl Street"] = "Алмрайвен — Док — Розетил"
    glossary = Glossary(entries=entries)

    block = glossary.to_prompt_block(texts=["I have lived in Almraiven all my life."])

    assert '"Almraiven"' in block
    # None of the hierarchical entries share specific districts with the
    # dialog, so the bare city prefix must not pull them all in.
    for idx in range(20):
        assert f"District {idx}" not in block
    assert "Rosetyl Street" not in block


def test_glossary_keeps_compound_entry_when_significant_component_matches():
    entries = {
        "Almraiven - Dock Ward - Rosetyl Street": "Алмрайвен — Док — Розетил",
        "Almraiven - Emerald Ward - Loom Avenue": "Алмрайвен — Изумруд — Ткацкая",
    }
    glossary = Glossary(entries=entries)

    block = glossary.to_prompt_block(
        texts=["The Rosetyl Street merchants gathered at the Dock Ward gates."]
    )

    assert "Rosetyl Street" in block
    assert "Loom Avenue" not in block
