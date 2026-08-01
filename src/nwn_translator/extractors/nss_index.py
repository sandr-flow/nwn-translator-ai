"""Module-wide index of ``.nss`` source string literals and their consumers.

The Aurora toolset packs the plain-text script source (``.nss``) next to the
compiled bytecode (``.ncs``) for every script it saves into a module. That
source states explicitly which engine function consumes each string literal,
so it is a far more reliable translatability oracle than bytecode heuristics.

The index parses every ``.nss`` under a module root once and answers, for a
given literal text: is it shown to the player, is it an internal identifier
(variable/tag/resref), or is it a comparison (dispatch) key? Author-defined
wrapper functions (``void Speak(object who, string text)``) are resolved
transitively by tracing their string parameters into engine calls.
"""

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine function argument tables
# ---------------------------------------------------------------------------
# Name-keyed counterparts of PLAYER_FACING_ACTIONS / NON_PLAYER_ACTIONS in
# ncs_extractor (routine ids verified against the game's nwscript.nss).
# Keys are (function name, zero-based argument position of the string).

PLAYER_ARG_POSITIONS: Set[Tuple[str, int]] = {
    ("SpeakString", 0),
    ("ActionSpeakString", 0),
    ("SendMessageToPC", 1),
    ("FloatingTextStringOnCreature", 0),
    ("SetCustomToken", 1),
    ("SetName", 1),
    ("SetDescription", 1),
    ("PostString", 1),
}

INTERNAL_ARG_POSITIONS: Set[Tuple[str, int]] = {
    ("PrintString", 0),
    ("WriteTimestampedLogEntry", 0),
    ("SendMessageToAllDMs", 0),
    ("SpawnScriptDebugger", 0),
    ("ExecuteScript", 0),
    ("GetItemPossessedBy", 1),
    ("CreateItemOnObject", 0),
    ("PlaySound", 0),
    ("GetWaypointByTag", 0),
    ("GetObjectByTag", 0),
    ("GetNearestObjectByTag", 0),
    ("CreateObject", 1),
    ("SpeakOneLinerConversation", 0),  # dialog resref, not display text
    ("ActionStartConversation", 1),  # dialog resref
    ("StartNewModule", 0),
    ("SetTag", 1),
    ("SetListenPattern", 1),
    ("DestroyCampaignDatabase", 0),
    ("StoreCampaignObject", 0),
    ("StoreCampaignObject", 1),
    ("RetrieveCampaignObject", 0),
    ("RetrieveCampaignObject", 1),
    ("PlaySpeakSoundByStrRef", 0),
    ("TagItemProperty", 1),
    ("GetModuleItemAcquiredBy", 0),
    ("FindSubString", 1),
    # Journal plot IDs must match the .jrl category tag verbatim; the display
    # text lives in the .jrl and is translated there.
    ("AddJournalQuestEntry", 0),
    ("RemoveJournalQuestEntry", 0),
    ("GetJournalQuestExperience", 0),
}

# Variable-name families: GetLocalInt(oObj, "VarName") and friends take the
# var name at position 1; campaign functions take db name + var name at 0, 1.
_LOCAL_VAR_PREFIXES = ("GetLocal", "SetLocal", "DeleteLocal")
_CAMPAIGN_PREFIXES = ("GetCampaign", "SetCampaign", "DeleteCampaign")

# Recursion cap for wrapper-into-wrapper resolution.
_WRAPPER_RESOLVE_DEPTH = 5

# Context window used for LLM-gate snippets (lines each side / char cap).
_NSS_SNIPPET_LINES = 20
_NSS_SNIPPET_CHAR_CAP = 2000

# Words that look like a call target but are control flow / declarations.
_NON_CALL_KEYWORDS = frozenset(
    {
        "if",
        "while",
        "for",
        "switch",
        "return",
        "do",
        "else",
        "case",
        "void",
        "int",
        "float",
        "string",
        "object",
        "effect",
        "event",
        "location",
        "itemproperty",
        "talent",
        "vector",
        "action",
        "struct",
        "const",
    }
)

_TYPE_KEYWORDS = (
    "void",
    "int",
    "float",
    "string",
    "object",
    "effect",
    "event",
    "location",
    "itemproperty",
    "talent",
    "vector",
    "action",
)

# `string Name(...)` / `struct xyz Name(...)` followed by an opening brace —
# a function *definition* (prototypes end with `;` and do not match).
_FUNC_DEF_RE = re.compile(
    r"\b(?:" + "|".join(_TYPE_KEYWORDS) + r"|struct\s+\w+)\s+(\w+)\s*\(([^)]*)\)\s*\{"
)


def classify_engine_arg(func: str, arg: int) -> Optional[str]:
    """Verdict for a string sitting at argument ``arg`` of engine call ``func``.

    Returns ``"player"``, ``"internal"``, or ``None`` when the function is not
    a known engine consumer (author-defined or irrelevant).
    """
    if (func, arg) in PLAYER_ARG_POSITIONS:
        return "player"
    if (func, arg) in INTERNAL_ARG_POSITIONS:
        return "internal"
    if arg == 1 and func.startswith(_LOCAL_VAR_PREFIXES):
        return "internal"
    if arg in (0, 1) and func.startswith(_CAMPAIGN_PREFIXES):
        return "internal"
    return None


# ---------------------------------------------------------------------------
# Lexical helpers
# ---------------------------------------------------------------------------


def strip_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments while preserving string literals."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : min(j + 1, n)])
            i = j + 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _skip_string(text: str, quote_idx: int) -> int:
    """Return the index just past the literal that opens at ``quote_idx``."""
    j = quote_idx + 1
    n = len(text)
    while j < n and text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, n)


@dataclass
class _Token:
    """A string literal or a bare-identifier argument found by the scanner."""

    kind: str  # "str" | "ident"
    value: str
    func: Optional[str]  # innermost enclosing call, if any
    arg: int  # zero-based argument position within that call
    is_compare: bool  # literal directly flanked by == or !=


def _scan(text: str) -> Iterator[_Token]:
    """Single pass over comment-stripped source tracking the call-arg stack.

    Yields every string literal with its syntactic position, and every bare
    identifier that constitutes a whole call argument (for wrapper tracing).
    """
    i, n = 0, len(text)
    stack: List[List] = []  # [callee name or None, current arg index]
    while i < n:
        c = text[i]
        if c == '"':
            end = _skip_string(text, i)
            lit = text[i + 1 : end - 1]
            func, arg = stack[-1] if stack else (None, -1)
            k = i - 1
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            before = text[max(0, k - 1) : k + 1]
            m = end
            while m < n and text[m] in " \t\r\n":
                m += 1
            after = text[m : m + 2]
            yield _Token("str", lit, func, arg, before in ("==", "!=") or after in ("==", "!="))
            i = end
            continue
        if c == "(":
            k = i - 1
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            end_name = k + 1
            while k >= 0 and (text[k].isalnum() or text[k] == "_"):
                k -= 1
            name: Optional[str] = text[k + 1 : end_name]
            if not name or name[0].isdigit() or name in _NON_CALL_KEYWORDS:
                name = None
            stack.append([name, 0])
        elif c == ")":
            if stack:
                stack.pop()
        elif c == "," and stack:
            stack[-1][1] += 1
        elif (c.isalpha() or c == "_") and stack:
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            ident = text[i:j]
            k = i - 1
            while k >= 0 and text[k] in " \t\r\n":
                k -= 1
            m = j
            while m < n and text[m] in " \t\r\n":
                m += 1
            # A whole argument (`f(x`, `x,`) or a concat operand (`s + "!"`):
            # NWScript's only string operator is `+`, so a `+`-boundary ident
            # still feeds the enclosing call's argument slot.
            if k >= 0 and text[k] in "(,+" and m < n and text[m] in ",)+":
                func, arg = stack[-1]
                if func:
                    yield _Token("ident", ident, func, arg, False)
            i = j
            continue
        i += 1


# `sVar = "..."` / `sVar += "..."`; (?!=) rejects `==`, and `!=`/`<=`/`>=`
# fail because their extra character sits between the identifier and `=`.
_ASSIGN_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\+?=(?!=)\s*")


def _iter_assigned_literals(text: str) -> Iterator[Tuple[str, str]]:
    """Yield ``(variable, literal)`` for literals in assignment concat chains.

    Handles ``sVar = "a" + IntToString(n) + "b";`` by walking the ``+`` chain
    and collecting every literal term. Local-variable speech is a major NCS
    pattern (random-bark tables assign to a var, then SpeakString(sVar)).
    """
    n = len(text)
    for m in _ASSIGN_RE.finditer(text):
        pos = m.end()
        while pos < n:
            c = text[pos]
            if c == '"':
                end = _skip_string(text, pos)
                yield m.group(1), text[pos + 1 : end - 1]
                pos = end
            elif c.isalnum() or c == "_" or c == ".":
                while pos < n and (text[pos].isalnum() or text[pos] in "_."):
                    pos += 1
                while pos < n and text[pos] in " \t\r\n":
                    pos += 1
                if pos < n and text[pos] == "(":
                    depth = 0
                    while pos < n:
                        if text[pos] == '"':
                            pos = _skip_string(text, pos)
                            continue
                        if text[pos] == "(":
                            depth += 1
                        elif text[pos] == ")":
                            depth -= 1
                            if depth == 0:
                                pos += 1
                                break
                        pos += 1
            else:
                break
            while pos < n and text[pos] in " \t\r\n":
                pos += 1
            if pos < n and text[pos] == "+":
                pos += 1
                while pos < n and text[pos] in " \t\r\n":
                    pos += 1
            else:
                break


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``open_idx`` (string-aware)."""
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def snippet_for_text(text: str, nss_content: str) -> Optional[str]:
    """Return a ±N-line window around the first literal occurrence of ``text``.

    Used to feed the LLM gate enough source context to decide whether the
    literal is player-facing or a technical identifier. Returns ``None`` when
    the literal is not found verbatim in ``nss_content``.
    """
    needle = f'"{text}"'
    idx = nss_content.find(needle)
    if idx == -1:
        return None

    lines = nss_content.splitlines()
    running = 0
    hit_line = 0
    for i, line in enumerate(lines):
        next_running = running + len(line) + 1  # +1 for the newline
        if running <= idx < next_running:
            hit_line = i
            break
        running = next_running

    start = max(0, hit_line - _NSS_SNIPPET_LINES)
    end = min(len(lines), hit_line + _NSS_SNIPPET_LINES + 1)
    snippet = "\n".join(lines[start:end])
    if len(snippet) > _NSS_SNIPPET_CHAR_CAP:
        # Keep the window centred on the hit line when trimming.
        hit_local = hit_line - start
        hit_offset = sum(len(lines[start + i]) + 1 for i in range(hit_local))
        half = _NSS_SNIPPET_CHAR_CAP // 2
        cut_start = max(0, hit_offset - half)
        cut_end = min(len(snippet), cut_start + _NSS_SNIPPET_CHAR_CAP)
        snippet = snippet[cut_start:cut_end]
    return snippet


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------


@dataclass
class _LiteralInfo:
    """Aggregated facts about one literal text across the whole module."""

    compare: bool = False
    roles: Set[str] = field(default_factory=set)  # "player" / "internal" / "mixed"
    player_consumer: Optional[str] = None  # representative function name
    files: List[Path] = field(default_factory=list)  # occurrence files, first first


class NssModuleIndex:
    """Verdicts for string literals, built from all ``.nss`` under one root."""

    def __init__(self) -> None:
        self._literals: Dict[str, _LiteralInfo] = {}
        self.source_count = 0
        # One-slot content cache: gate snippets for one script are usually
        # requested back to back from the same source file.
        self._snippet_cache: Optional[Tuple[Path, str]] = None

    # -- construction -------------------------------------------------------

    @classmethod
    def build(cls, root: Path, encoding: str = "cp1252") -> "NssModuleIndex":
        index = cls()
        wrapper_consumers: Dict[Tuple[str, int], Set[Tuple[str, int]]] = {}
        occurrences: List[Tuple[str, Optional[str], int, bool, Path]] = []
        # Per-file local-variable flow: assignments var -> literals, and
        # var -> consuming (func, arg) positions. File scope is a deliberate
        # over-approximation of NWScript's function scope.
        var_flows: List[Tuple[Dict[str, Set[str]], Dict[str, Set[Tuple[str, int]]], Path]] = []

        for path in sorted(root.glob("*.nss")):
            try:
                raw = path.read_bytes().decode(encoding, errors="replace")
            except OSError:
                continue
            index.source_count += 1
            stripped = strip_comments(raw)

            ident_consumers: Dict[str, Set[Tuple[str, int]]] = {}
            for tok in _scan(stripped):
                if tok.kind == "str":
                    occurrences.append((tok.value, tok.func, tok.arg, tok.is_compare, path))
                elif tok.func is not None:
                    ident_consumers.setdefault(tok.value, set()).add((tok.func, tok.arg))

            assignments: Dict[str, Set[str]] = {}
            for var, literal in _iter_assigned_literals(stripped):
                if literal.strip():
                    assignments.setdefault(var, set()).add(literal)
            if assignments:
                var_flows.append((assignments, ident_consumers, path))

            for m in _FUNC_DEF_RE.finditer(stripped):
                fname, params = m.group(1), m.group(2)
                str_params: Dict[str, int] = {}
                for pos, param in enumerate(params.split(",")):
                    tokens = param.split("=")[0].split()
                    if len(tokens) >= 2 and tokens[0] == "string":
                        str_params[tokens[-1]] = pos
                if not str_params:
                    continue
                body_open = m.end() - 1
                body = stripped[body_open : _match_brace(stripped, body_open) + 1]
                for tok in _scan(body):
                    if tok.kind == "ident" and tok.value in str_params and tok.func != fname:
                        wrapper_consumers.setdefault((fname, str_params[tok.value]), set()).add(
                            (tok.func or "", tok.arg)
                        )

        resolve_cache: Dict[Tuple[str, int], str] = {}

        def resolve(func: str, arg: int, depth: int, seen: Set[Tuple[str, int]]) -> str:
            engine = classify_engine_arg(func, arg)
            if engine is not None:
                return engine
            key = (func, arg)
            if key in resolve_cache:
                return resolve_cache[key]
            if key not in wrapper_consumers or depth >= _WRAPPER_RESOLVE_DEPTH or key in seen:
                return "unknown"
            seen = seen | {key}
            roles = {
                resolve(cf, ca, depth + 1, seen) for cf, ca in wrapper_consumers[key] if cf
            } - {"unknown"}
            if "mixed" in roles or roles == {"player", "internal"}:
                verdict = "mixed"
            elif roles == {"player"}:
                verdict = "player"
            elif roles == {"internal"}:
                verdict = "internal"
            else:
                verdict = "unknown"
            resolve_cache[key] = verdict
            return verdict

        for value, func, arg, is_compare, path in occurrences:
            info = index._literals.setdefault(value, _LiteralInfo())
            if path not in info.files:
                info.files.append(path)
            if is_compare:
                info.compare = True
                continue
            if func is None:
                continue
            role = resolve(func, arg, 0, set())
            if role in ("player", "internal", "mixed"):
                info.roles.add(role)
                if role == "player" and info.player_consumer is None:
                    info.player_consumer = func

        # Literals that reach a consumer through a local variable:
        # sSpeak = "..."; ... SpeakString(sSpeak);
        for assignments, ident_consumers, path in var_flows:
            for var, literals in assignments.items():
                consumer_roles: Set[str] = set()
                consumer_func: Optional[str] = None
                for cf, ca in ident_consumers.get(var, set()):
                    role = resolve(cf, ca, 0, set())
                    if role != "unknown":
                        consumer_roles.add(role)
                        if role == "player" and consumer_func is None:
                            consumer_func = cf
                if not consumer_roles:
                    continue
                if "mixed" in consumer_roles or {"player", "internal"} <= consumer_roles:
                    var_role = "mixed"
                else:
                    var_role = next(iter(consumer_roles))
                for literal in literals:
                    info = index._literals.setdefault(literal, _LiteralInfo())
                    if path not in info.files:
                        info.files.append(path)
                    info.roles.add(var_role)
                    if var_role == "player" and info.player_consumer is None:
                        info.player_consumer = consumer_func
        return index

    # -- queries ------------------------------------------------------------

    def verdict(self, text: str) -> str:
        """One of ``player | internal | compare | mixed | unknown | absent``.

        ``compare`` wins outright: a literal ever used as a dispatch key must
        never be translated, even if it is also spoken somewhere.
        """
        info = self._literals.get(text)
        if info is None:
            return "absent"
        if info.compare:
            return "compare"
        if "mixed" in info.roles or {"player", "internal"} <= info.roles:
            return "mixed"
        if info.roles == {"player"}:
            return "player"
        if info.roles == {"internal"}:
            return "internal"
        return "unknown"

    def player_consumer(self, text: str) -> Optional[str]:
        info = self._literals.get(text)
        return info.player_consumer if info else None

    def snippet(
        self, text: str, encoding: str = "cp1252", prefer_stem: Optional[str] = None
    ) -> Optional[str]:
        """LLM-gate context window around ``text`` in module source.

        Prefers the occurrence in the file whose stem matches ``prefer_stem``
        (i.e. the very script being extracted), falling back to the first
        occurrence anywhere in the module.
        """
        info = self._literals.get(text)
        if info is None or not info.files:
            return None
        target = info.files[0]
        if prefer_stem is not None:
            lowered = prefer_stem.lower()
            for path in info.files:
                if path.stem.lower() == lowered:
                    target = path
                    break
        if self._snippet_cache is not None and self._snippet_cache[0] == target:
            raw = self._snippet_cache[1]
        else:
            try:
                raw = target.read_bytes().decode(encoding, errors="replace")
            except OSError:
                return None
            self._snippet_cache = (target, raw)
        return snippet_for_text(text, raw)


# ---------------------------------------------------------------------------
# Per-module cache
# ---------------------------------------------------------------------------

_CACHE_MAX = 4
_index_cache: "OrderedDict[Tuple[Path, str], NssModuleIndex]" = OrderedDict()
# Extraction runs .ncs files through a thread pool; without a lock the first
# wave of cache misses would build the same module index once per worker.
_index_lock = threading.Lock()


def get_module_index(root: Path, encoding: str = "cp1252") -> NssModuleIndex:
    """Build (or fetch a cached) index for the module extracted at ``root``."""
    key = (root.resolve(), encoding)
    cached = _index_cache.get(key)
    if cached is not None:
        _index_cache.move_to_end(key)
        return cached
    with _index_lock:
        cached = _index_cache.get(key)
        if cached is not None:
            _index_cache.move_to_end(key)
            return cached
        index = NssModuleIndex.build(root, encoding)
        logger.debug(
            "NSS index for %s: %d sources, %d unique literals",
            root,
            index.source_count,
            len(index._literals),
        )
        _index_cache[key] = index
        while len(_index_cache) > _CACHE_MAX:
            _index_cache.popitem(last=False)
    return index


def clear_index_cache() -> None:
    """Drop all cached module indexes (tests / long-lived processes)."""
    _index_cache.clear()
