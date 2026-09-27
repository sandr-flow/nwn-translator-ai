"""Preparation and request planning of contextual dialog translation.

Everything here is pure: it turns a parsed dialog into its lines and scripts
and decides which lines share a request, without calling the model.
:class:`~nwn_translator.translators.context_translator.ContextualTranslationManager`
sends the requests.

The limits are counted in prompt characters, not tokens, because tokenizers
differ between models; they are conservative so that the JSON answer stays
well inside the output budget even with reasoning.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ..context.dialog_formatter import format_dialog_tree, format_nodes, iter_nodes
from ..extractors.base import DialogNode, Occurrence, occurrence_key
from ..extractors.dialog_extractor import DialogExtractor, dialog_item_id
from ..glossary import GLOSSARY_MAX_CHARS, Glossary, terminology_block
from .token_handler import TokenHandler, sanitize_text

#: Largest script of one single-file request; larger dialogs are chunked.
CHUNK_TARGET_CHARS = 24000
#: Most lines in one single-file request.
CHUNK_MAX_KEYS = 120
#: Largest full script (with its ``[E0] [NPC]`` markup) of a dialog that may
#: share a grouped request with other small dialogs.
SMALL_DIALOG_CHARS = 2000
#: Largest combined script of a grouped request.
GROUP_TARGET_CHARS = 8000
#: Most files in a grouped request.
GROUP_MAX_FILES = 12


@dataclass
class PreparedDialog:
    """A dialog parsed into its lines, ready for translation requests.

    Attributes:
        file_path: Path of the ``.dlg`` resource.
        item_budget: Progress units the pipeline counts for this file (its
            extracted item count).
        node_map: Every node reachable from ``StartingList`` by script key, in
            :func:`~nwn_translator.context.dialog_formatter.iter_nodes` order.
        texts: Source text of every node with non-blank text, by key, in the
            same order. These are the lines to translate.
        sanitized: Text sent to the model for each line, with NWN tokens and
            tags replaced by placeholders.
        handlers: Token handler of each line; it restores and validates the answer.
        script: The whole dialog rendered with the sanitized texts.
    """

    file_path: Path
    item_budget: int
    node_map: Dict[str, DialogNode]
    texts: Dict[str, str]
    sanitized: Dict[str, str]
    handlers: Dict[str, TokenHandler]
    script: str

    @property
    def keys(self) -> List[str]:
        """Keys of the lines to translate, in walk order."""
        return list(self.texts)

    def address(self, key: str) -> Occurrence:
        """Returns the occurrence the extractor assigned to a line (``E3`` -> ``stem:entry:3``)."""
        stem = self.file_path.stem
        return occurrence_key(self.file_path, dialog_item_id(stem, key.startswith("E"), key[1:]))


class Chunk(NamedTuple):
    """The lines of one single-file request.

    Attributes:
        keys: Keys of the lines to translate.
        script: The script that shows them.
    """

    keys: List[str]
    script: str


def prepare_dialog(
    file_path: Path,
    parsed_data: Dict[str, Any],
    item_budget: int,
    *,
    preserve_tokens: bool,
) -> Optional[PreparedDialog]:
    """Builds a dialog's conversation tree and sanitizes its lines.

    Args:
        file_path: Path of the ``.dlg`` resource.
        parsed_data: Parsed GFF root struct.
        item_budget: Progress units the pipeline counts for this file.
        preserve_tokens: Protect standard NWN tokens as well as tags
            (``TranslationConfig.preserve_tokens``).

    Returns:
        The prepared dialog, or ``None`` when no node is reachable from
        ``StartingList``.
    """
    tree = DialogExtractor().build_dialog_tree(parsed_data)
    if not tree:
        return None
    node_map = dict(iter_nodes(tree))
    texts: Dict[str, str] = {}
    sanitized: Dict[str, str] = {}
    handlers: Dict[str, TokenHandler] = {}
    for key, node in node_map.items():
        if node.text and node.text.strip():
            texts[key] = node.text
            sanitized[key], handlers[key] = sanitize_text(
                node.text, preserve_tokens=preserve_tokens
            )
    script = format_dialog_tree(tree, sanitized)
    return PreparedDialog(file_path, item_budget, node_map, texts, sanitized, handlers, script)


def plan_chunks(
    dialog: PreparedDialog,
    keys: List[str],
    target_lang: str,
    glossary: Optional[Glossary],
) -> List[Chunk]:
    """Splits the lines of one dialog into requests.

    When *keys* are all the dialog's lines, the whole dialog script is sent;
    otherwise only the nodes of *keys* with their neighbours as context. If that
    exceeds :data:`CHUNK_TARGET_CHARS`, :data:`CHUNK_MAX_KEYS` or a glossary
    block of ``GLOSSARY_MAX_CHARS``, lines are packed greedily in *keys* order
    into chunks of selected nodes, each within the same limits unless a single
    line exceeds them.

    Args:
        dialog: The prepared dialog.
        keys: Keys of the lines to request, in walk order.
        target_lang: Target language (selects the glossary terms).
        glossary: Run glossary, if any.

    Returns:
        The chunks, in request order.
    """

    def script_of(chunk_keys: List[str]) -> str:
        """Renders the selected nodes of *chunk_keys* with their neighbours."""
        return format_nodes(chunk_keys, dialog.node_map, dialog.sanitized)

    def fits(script: str) -> bool:
        """Tells whether *script* and its glossary block stay within the limits."""
        return (
            len(script) <= CHUNK_TARGET_CHARS
            and len(terminology_block([script], target_lang, glossary)) <= GLOSSARY_MAX_CHARS
        )

    whole = dialog.script if set(keys) == set(dialog.texts) else script_of(keys)
    if len(keys) <= CHUNK_MAX_KEYS and fits(whole):
        return [Chunk(list(keys), whole)]

    chunks: List[Chunk] = []
    current: List[str] = []
    for key in keys:
        if current and (len(current) >= CHUNK_MAX_KEYS or not fits(script_of(current + [key]))):
            chunks.append(Chunk(current, script_of(current)))
            current = []
        current.append(key)
    if current:
        chunks.append(Chunk(current, script_of(current)))
    return chunks


def pack_groups(
    small: List[PreparedDialog],
    target_lang: str,
    glossary: Optional[Glossary],
) -> Tuple[List[List[PreparedDialog]], List[PreparedDialog]]:
    """Packs small dialogs greedily, in order, into grouped requests.

    A group closes before a dialog that would push it past
    :data:`GROUP_TARGET_CHARS` of scripts, :data:`GROUP_MAX_FILES` files or a
    glossary block of ``GLOSSARY_MAX_CHARS``.

    Args:
        small: Dialogs whose script fits :data:`SMALL_DIALOG_CHARS`.
        target_lang: Target language (selects the glossary terms).
        glossary: Run glossary, if any.

    Returns:
        Groups of two or more dialogs, and the dialogs left alone, both in
        packing order.
    """
    packs: List[List[PreparedDialog]] = []
    current: List[PreparedDialog] = []
    chars = 0
    for dialog in small:
        scripts = [d.script for d in current] + [dialog.script]
        if current and (
            chars + len(dialog.script) > GROUP_TARGET_CHARS
            or len(current) >= GROUP_MAX_FILES
            or len(terminology_block(scripts, target_lang, glossary)) > GLOSSARY_MAX_CHARS
        ):
            packs.append(current)
            current, chars = [], 0
        current.append(dialog)
        chars += len(dialog.script)
    if current:
        packs.append(current)
    return [p for p in packs if len(p) > 1], [p[0] for p in packs if len(p) == 1]


def plan_requests(
    dialogs: List[PreparedDialog],
    target_lang: str,
    glossary: Optional[Glossary],
) -> Tuple[List[PreparedDialog], List[List[PreparedDialog]]]:
    """Chooses which dialogs get their own request and which share one.

    Args:
        dialogs: Dialogs with lines to translate, in pipeline order.
        target_lang: Target language (selects the glossary terms).
        glossary: Run glossary, if any.

    Returns:
        The single-file dialogs (large ones in input order, then small ones
        that found no partner) and the groups, in packing order.
    """
    large = [d for d in dialogs if len(d.script) > SMALL_DIALOG_CHARS]
    small = [d for d in dialogs if len(d.script) <= SMALL_DIALOG_CHARS]
    groups, loners = pack_groups(small, target_lang, glossary)
    return large + loners, groups
