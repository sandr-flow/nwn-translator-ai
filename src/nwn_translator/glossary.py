"""Run-wide glossary of canonical translations for world proper names.

:class:`Glossary` maps English source forms of NPC, location, item and quest
names to their canonical translations. :func:`terminology_block` renders the
entries a batch of texts mentions, merged with the static race terms, into every
translation prompt. The glossary is built once per run by
:mod:`nwn_translator.glossary_builder`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, Optional, Set

from .race_dictionary import RACE_TERMS

#: Character budget of the GLOSSARY block of one translation prompt.
GLOSSARY_MAX_CHARS = 6000

#: Quotation marks a game string may be wrapped in; models answer without them.
QUOTE_CHARS = '"“”«»'


class _TermMatcher:
    """Word-bounded, case-insensitive search for complete source forms, memoized per text.

    A form matches a batch iff it matches one of the batch's texts, so callers
    take the union of per-text results instead of rescanning a joined corpus.
    The ``str.lower`` substring prefilter skips the regex for absent forms; the
    exotic equivalences of ``re.IGNORECASE`` (long s, Kelvin sign) are not
    matched, which is acceptable for game text.
    """

    def __init__(self, keys: Iterable[str]) -> None:
        """Compiles one pattern per source form.

        Args:
            keys: Source forms to search for.
        """
        self._patterns = {
            key: (key.lower(), re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)", re.IGNORECASE))
            for key in keys
        }
        self._memo: Dict[str, FrozenSet[str]] = {}

    def keys_in(self, text: str) -> FrozenSet[str]:
        """Returns the forms that occur in *text* as whole words.

        Args:
            text: One source text.

        Returns:
            The matching source forms.
        """
        found = self._memo.get(text)
        if found is None:
            lowered = text.lower()
            found = frozenset(
                key
                for key, (needle, pattern) in self._patterns.items()
                if needle in lowered and pattern.search(text)
            )
            self._memo[text] = found
        return found


@dataclass
class Glossary:
    """Canonical English -> target-language mappings for world proper names.

    ``entries`` and ``aliases`` are not modified after matching starts: the
    matcher and the per-language merged glossaries are derived from them.

    Attributes:
        entries: Source form -> canonical translation.
        aliases: Alias source form -> the source form of its entity (root).
    """

    entries: Dict[str, str] = field(default_factory=dict)

    aliases: Dict[str, str] = field(default_factory=dict)

    _matcher: Optional[_TermMatcher] = field(default=None, init=False, repr=False, compare=False)
    _with_terms: Dict[str, "Glossary"] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def matching_entries(self, texts: Iterable[str]) -> Dict[str, str]:
        """Returns the entries whose source form occurs in *texts*, with their alias families.

        Only explicit aliases share an entity; a shared word does not.

        Args:
            texts: Source texts of one prompt; empty items are ignored.

        Returns:
            Matching entries in ``entries`` order.
        """
        if self._matcher is None:
            self._matcher = _TermMatcher(self.entries)
        matches: Set[str] = set()
        for text in texts:
            if text:
                matches |= self._matcher.keys_in(str(text))
        roots = {self.aliases.get(key, key) for key in matches}
        return {
            key: value
            for key, value in self.entries.items()
            if key in matches or self.aliases.get(key, key) in roots
        }

    def to_prompt_block(self, texts: Optional[Iterable[str]] = None) -> str:
        """Renders the GLOSSARY prompt block.

        Args:
            texts: Restrict the block to :meth:`matching_entries` of these texts;
                ``None`` renders every entry.

        Returns:
            The block with entries in code-point order of their source forms, or
            ``""`` when no entry applies.
        """
        entries = self.entries if texts is None else self.matching_entries(texts)
        if not entries:
            return ""
        lines = [
            "GLOSSARY (distinct entities; use these canonical forms consistently. "
            "Inflect as required by the current context. An alias may have its own "
            "abbreviated or deliberately distorted form; do not expand it automatically):"
        ]
        for source, target in sorted(entries.items()):
            alias = self.aliases.get(source)
            relation = f" (alias of {alias})" if alias else ""
            lines.append(f'  * "{source}" → {target}{relation}')
        return "\n".join(lines)


_NO_GLOSSARY = Glossary()


def terminology_block(texts: Iterable[str], target_lang: str, glossary: Optional[Glossary]) -> str:
    """Renders the terminology a translation prompt needs for *texts*.

    The glossary is merged with the static race terms of *target_lang*, which
    win over glossary entries with the same casefolded source form. The merged
    glossary is built once per glossary and language, so its match memo serves
    every later prompt.

    Args:
        texts: Source texts of the prompt.
        target_lang: Target language name (case-insensitive).
        glossary: The run's glossary, if any.

    Returns:
        The GLOSSARY block for *texts*, or ``""``.
    """
    source = glossary if glossary is not None else _NO_GLOSSARY
    lang = target_lang.lower()
    merged = source._with_terms.get(lang)
    if merged is None:
        static = RACE_TERMS.get(lang, {})
        entries = {
            key: value for key, value in source.entries.items() if key.casefold() not in static
        }
        entries.update(static)
        merged = source._with_terms[lang] = Glossary(entries, source.aliases)
    return merged.to_prompt_block(texts)


def restore_wrapping_quotes(key: str, value: str) -> str:
    """Gives *value* back the quotation marks *key* is wrapped in.

    A translation replaces the whole game string, so a name the module author
    wrote as ``"Thesis Paper Room"`` must keep its quotes in the patched module,
    even though the model answers without them.

    Args:
        key: Source string.
        value: Its translation.

    Returns:
        *value*, wrapped in the first and last character of *key* when *key* is
        quoted and *value* is not.
    """
    if len(key) < 2 or key[0] not in QUOTE_CHARS or key[-1] not in QUOTE_CHARS:
        return value
    if len(value) >= 2 and value[0] in QUOTE_CHARS and value[-1] in QUOTE_CHARS:
        return value
    return f"{key[0]}{value}{key[-1]}"
