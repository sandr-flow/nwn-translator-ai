"""Protection of NWN tokens and inline markup across a model translation.

Before a request, :func:`sanitize_text` swaps every protected artifact of a string
for an opaque placeholder: engine tokens (``<FirstName>``, ``<CustomToken:123>``),
inline tags (``<StartAction>`` … ``</Start>``), the markers of dialog actions
(``<<…>>``, ``-…-``) and other angle fragments. The returned :class:`TokenHandler`
restores the placeholders in the model's answer, checks that the answer carries
exactly the source's artifacts in source order and, when asked, cleans up an
answer that does not while keeping its prose.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

#: Non-European scripts (CJK, Hebrew, Arabic, Devanagari, Thai). Target
#: languages are European, so a translation containing these characters when
#: the source does not is a model glitch: the injector would silently drop
#: them, garbling the text (observed: Chinese and Thai chars inside Russian).
FOREIGN_SCRIPT_PATTERN = re.compile(
    "[\\u0590-\\u06ff\\u0900-\\u097f\\u0e00-\\u0e7f"
    "\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff\\uac00-\\ud7af\\uf900-\\ufaff]"
)

# Artifact shapes, in the order the scanner tries them at each position.
_DIALOG_ACTION = r"<<[^<>\r\n]+>>"
# The body needs a Unicode letter, so a translated marker ("-далее-") still
# matches, and the dashes hug it: action markers read "-sighs-", while prose
# dashes ("go - your job") are spaced.
_DASH_ACTION = r"(?<!\w)-(?!\s)[^-\r\n]*[^\W\d_][^-\r\n]*(?<!\s)-(?!\w)"
_INLINE_TAG = r"</?Start[A-Za-z]*>"
_ENGINE_TOKEN = r"<[A-Za-z][A-Za-z0-9_]*(?:[/:][A-Za-z0-9_]+)*>"
_ANGLE_FRAGMENT = r"<[^<>\r\n]+>"

_DIALOG_ACTION_RE = re.compile(_DIALOG_ACTION)
_DASH_ACTION_RE = re.compile(_DASH_ACTION)
_INLINE_TAG_RE = re.compile(_INLINE_TAG)
_ENGINE_TOKEN_RE = re.compile(_ENGINE_TOKEN)
_ANGLE_FRAGMENT_RE = re.compile(_ANGLE_FRAGMENT)
_TOKEN_LIKE_RE = re.compile("|".join((_INLINE_TAG, _ENGINE_TOKEN, _ANGLE_FRAGMENT)))
_ARTIFACT_RE = re.compile(
    "|".join((_DIALOG_ACTION, _DASH_ACTION, _INLINE_TAG, _ENGINE_TOKEN, _ANGLE_FRAGMENT))
)

#: Artifact kinds whose placeholders carry the ``NWN_INLINE`` prefix.
_INLINE_KINDS = frozenset({"inline_tag", "dialog_action_marker", "dash_action_marker"})

# Wrappers a model may put around a placeholder core when it echoes it back.
_WRAPPERS = (("__", "__"), (r"\[\[", r"\]\]"), (r"<<\[", r"\]>>"), (r"<\[", r"\]>"))


def _wrapped(core: str) -> str:
    """Returns an alternation of *core* inside every placeholder wrapper."""
    return "|".join(left + core + right for left, right in _WRAPPERS)


_INLINE_CORE = r"NWN_INLINE_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
_TOKEN_CORE = r"NWN_TOKEN_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
_ANY_CORE = f"(?:{_INLINE_CORE}|{_TOKEN_CORE})"
# Placeholder matching ignores case: models occasionally re-case placeholders
# (``__nwn_token_…__``), and a missed match either leaks the raw placeholder into
# the output or silently drops the token.
_INLINE_PLACEHOLDER_RE = re.compile(_wrapped(f"({_INLINE_CORE})"), re.IGNORECASE)
_TOKEN_PLACEHOLDER_RE = re.compile(_wrapped(f"({_TOKEN_CORE})"), re.IGNORECASE)
_PLACEHOLDER_NOISE_RE = re.compile(_wrapped(_ANY_CORE), re.IGNORECASE)
_BARE_PLACEHOLDER_NOISE_RE = re.compile(rf"[^\w\s]*{_ANY_CORE}[^\w\s]*", re.IGNORECASE)
# Last-resort barrier: any leftover blob still carrying the placeholder marker
# (mangled core, stray wrapper) must never reach the output file.
_PLACEHOLDER_RESIDUE_RE = re.compile(r"\S*NWN_(?:TOKEN|INLINE)\S*", re.IGNORECASE)
# Exactly the cores sanitize() writes (8-hex nonce, decimal counter). A looser
# core would swallow a word between two placeholders
# (``__NWN_INLINE_x_0__Attack__NWN_INLINE_x_1__``) and call the string empty.
_EXACT_PLACEHOLDER_RE = re.compile(_wrapped(r"(?:NWN_INLINE|NWN_TOKEN)_[0-9a-f]{8}_\d+"))


def normalize_translated_text(text: str) -> str:
    """NFC-normalizes model output and drops stray combining marks.

    Models occasionally emit combining accents (e.g. U+0301 in ``Тиндало́са``)
    that no single-byte NWN code page can encode. NFC runs first so precomposed
    forms (``é``, ``й``) survive; any combining mark left after composition is
    dropped.

    Args:
        text: Model output.

    Returns:
        The normalized text.
    """
    if not text:
        return text
    composed = unicodedata.normalize("NFC", text)
    if not any(unicodedata.combining(ch) for ch in composed):
        return composed
    return "".join(ch for ch in composed if not unicodedata.combining(ch))


def has_translatable_content(sanitized: str) -> bool:
    """Tells whether a Unicode letter or digit remains in *sanitized* without its placeholders.

    Args:
        sanitized: Output of :func:`sanitize_text`; whitespace, punctuation and
            underscores do not count.

    Returns:
        ``True`` when there is something to translate.
    """
    if not sanitized:
        return False
    return re.search(r"[^\W_]", _EXACT_PLACEHOLDER_RE.sub("", sanitized)) is not None


@dataclass(frozen=True)
class PreservedArtifact:
    """One protected artifact of a source text.

    Attributes:
        kind: ``engine_token``, ``angle_fragment``, ``inline_tag``,
            ``dialog_action_marker`` or ``dash_action_marker``.
        original: The artifact as written in the source.
        placeholder: The placeholder standing for it in the sanitized text.
    """

    kind: str
    original: str
    placeholder: str


@dataclass
class TokenMismatchReport:
    """Comparison of the source's artifacts with those of a restored answer.

    Attributes:
        is_exact_match: The answer carries exactly the source's artifacts in order.
        mismatch_type: ``exact_match``, ``count_mismatch``, ``order_mismatch``,
            ``value_mismatch`` or ``foreign_script``.
        expected_sequence: Artifacts of the source, in order.
        actual_sequence: Artifacts found in the restored answer, in order.
    """

    is_exact_match: bool
    mismatch_type: str
    expected_sequence: List[str] = field(default_factory=list)
    actual_sequence: List[str] = field(default_factory=list)


@dataclass
class TokenProcessingResult:
    """Outcome of restoring, validating and optionally cleaning up one answer.

    Attributes:
        final_text: The text to use: the restored answer, or its cleaned form.
        exact_valid: The restored answer matched the source's artifacts exactly.
        used_cleanup: Mismatched artifacts were removed to produce *final_text*.
        mismatch_report: Validation report of the restored answer.
    """

    final_text: str
    exact_valid: bool
    used_cleanup: bool
    mismatch_report: TokenMismatchReport


def _classify(raw: str, preserve_tokens: bool) -> Optional[str]:
    """Returns the artifact kind of a token-like fragment, or ``None`` to keep it as text."""
    if _INLINE_TAG_RE.fullmatch(raw):
        return "inline_tag"
    if _ENGINE_TOKEN_RE.fullmatch(raw):
        return "engine_token" if preserve_tokens else None
    if _ANGLE_FRAGMENT_RE.fullmatch(raw):
        return "angle_fragment"
    return None


def _scan(text: str, preserve_tokens: bool) -> List[Tuple[int, int, str]]:
    """Returns ``(start, end, kind)`` of every protected artifact of *text*, in order.

    A dialog (``<<…>>``) or dash (``-…-``) action contributes its two markers, so
    the action text between them stays translatable; tokens and inline tags nested
    in a dash action are artifacts of their own. Engine tokens count only with
    *preserve_tokens*.
    """
    spans: List[Tuple[int, int, str]] = []
    for match in _ARTIFACT_RE.finditer(text):
        raw = match.group(0)
        start, end = match.span()
        if _DIALOG_ACTION_RE.fullmatch(raw):
            spans.append((start, start + 2, "dialog_action_marker"))
            spans.append((end - 2, end, "dialog_action_marker"))
        elif _DASH_ACTION_RE.fullmatch(raw):
            spans.append((start, start + 1, "dash_action_marker"))
            for nested in _TOKEN_LIKE_RE.finditer(raw[1:-1]):
                kind = _classify(nested.group(0), preserve_tokens)
                if kind is not None:
                    spans.append((start + 1 + nested.start(), start + 1 + nested.end(), kind))
            spans.append((end - 1, end, "dash_action_marker"))
        else:
            kind = _classify(raw, preserve_tokens)
            if kind is not None:
                spans.append((start, end, kind))
    return spans


def _compare(expected: List[str], actual: List[str]) -> TokenMismatchReport:
    """Classifies how the *actual* artifact sequence differs from *expected*."""
    if expected == actual:
        mismatch_type = "exact_match"
    elif len(expected) != len(actual):
        mismatch_type = "count_mismatch"
    elif sorted(expected) == sorted(actual):
        mismatch_type = "order_mismatch"
    else:
        mismatch_type = "value_mismatch"
    return TokenMismatchReport(expected == actual, mismatch_type, expected, actual)


def _strip_placeholder_noise(text: str) -> str:
    """Removes wrapped and bare placeholder cores that map to no artifact."""
    return _BARE_PLACEHOLDER_NOISE_RE.sub("", _PLACEHOLDER_NOISE_RE.sub("", text))


def _has_unbalanced_action_tags(text: str) -> bool:
    """Tells whether the ``<Start…>``/``</Start>`` tags of *text* fail to nest properly."""
    depth = 0
    for tag in _INLINE_TAG_RE.findall(text):
        depth += -1 if tag.startswith("</") else 1
        if depth < 0:
            return True
    return depth != 0


def _normalize_cleanup_whitespace(text: str) -> str:
    """Minimally normalizes the whitespace left behind by removed artifacts."""
    normalized = re.sub(r"[ \t]+\n", "\n", text)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = re.sub(r" +([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"\( ", "(", normalized)
    normalized = re.sub(r" \)", ")", normalized)
    return normalized.strip()


class TokenHandler:
    """Placeholder handler of one source text: sanitizing, restoring, validating, cleaning up.

    Attributes:
        preserve_tokens: Protect engine tokens (``<FirstName>``); inline tags,
            action markers and angle fragments are always protected.
        artifacts: Protected artifacts of the last sanitized text, in order.
    """

    def __init__(self, preserve_tokens: bool = True):
        """Creates a handler.

        Args:
            preserve_tokens: Protect engine tokens as well as inline markup.
        """
        self.preserve_tokens = preserve_tokens
        self.artifacts: List[PreservedArtifact] = []
        self._source = ""
        self._nonce = ""
        #: Lowercased placeholder core -> artifact original.
        self._originals: Dict[str, str] = {}

    def sanitize(self, text: str) -> str:
        """Replaces the protected artifacts of *text* with placeholders.

        Placeholders are deterministic: equal texts sanitize to equal strings, so
        they share one deduplicated request. The artifacts are kept in
        :attr:`artifacts`.

        Args:
            text: Source text.

        Returns:
            The sanitized text.
        """
        self._source = text or ""
        self.artifacts = []
        self._originals = {}
        if not text:
            return self._source
        self._nonce = hashlib.blake2s(text.encode("utf-8"), digest_size=4).hexdigest()
        parts: List[str] = []
        last_end = 0
        for start, end, kind in _scan(text, self.preserve_tokens):
            parts.append(text[last_end:start])
            parts.append(self._protect(text[start:end], kind))
            last_end = end
        parts.append(text[last_end:])
        return "".join(parts)

    def restore(self, text: str) -> str:
        """Puts the protected artifacts back in place of their placeholders.

        Unknown or mangled placeholders are dropped; Start-tags are dropped as well
        when the answer no longer nests them and differs from the source's tags.

        Args:
            text: Model answer.

        Returns:
            The restored text.
        """
        if not text:
            return ""
        restored = _INLINE_PLACEHOLDER_RE.sub(self._original_for, text)
        restored = _TOKEN_PLACEHOLDER_RE.sub(self._original_for, restored)
        restored = _PLACEHOLDER_RESIDUE_RE.sub("", _strip_placeholder_noise(restored))
        return self._drop_deviating_action_tags(restored)

    def validate_text(self, restored: str) -> TokenMismatchReport:
        """Compares the artifacts of a restored answer (see :meth:`restore`) with the source's."""
        actual = [restored[start:end] for start, end, _kind in _scan(restored, True)]
        return _compare(self.get_expected_artifact_sequence(), actual)

    def finalize_translation(
        self, translated_text: str, *, allow_cleanup: bool = False
    ) -> TokenProcessingResult:
        """Restores a model answer, validates it and optionally cleans it up.

        An answer that brings in a foreign script the source lacks is invalid even
        when its artifacts match.

        Args:
            translated_text: Model answer.
            allow_cleanup: Remove mismatched artifacts (and foreign-script
                characters) instead of only reporting the mismatch.

        Returns:
            The final text and how it was obtained.
        """
        restored = self.restore(normalize_translated_text(translated_text))
        report = self.validate_text(restored)
        foreign_script = bool(FOREIGN_SCRIPT_PATTERN.search(restored)) and not bool(
            FOREIGN_SCRIPT_PATTERN.search(self._source)
        )
        if foreign_script and report.is_exact_match:
            report = replace(report, is_exact_match=False, mismatch_type="foreign_script")
        if report.is_exact_match or not allow_cleanup:
            return TokenProcessingResult(restored, report.is_exact_match, False, report)
        cleaned = restored
        if report.mismatch_type != "foreign_script":
            cleaned = self.cleanup_mismatched_artifacts(restored)
        if foreign_script:
            cleaned = FOREIGN_SCRIPT_PATTERN.sub("", cleaned)
        return TokenProcessingResult(cleaned, False, True, report)

    def cleanup_mismatched_artifacts(self, restored: str) -> str:
        """Keeps the source's artifacts in order and drops every other token-like fragment.

        Args:
            restored: Output of :meth:`restore` that failed validation.

        Returns:
            The cleaned text with normalized whitespace.
        """
        if not restored:
            return ""
        cleaned = _strip_placeholder_noise(restored)
        expected = self.get_expected_artifact_sequence()
        expected_index = 0
        cursor = 0
        parts: List[str] = []
        for match in _TOKEN_LIKE_RE.finditer(cleaned):
            raw = match.group(0)
            parts.append(cleaned[cursor : match.start()])
            if expected_index < len(expected) and raw == expected[expected_index]:
                parts.append(raw)
                expected_index += 1
            cursor = match.end()
        parts.append(cleaned[cursor:])
        cleaned = _PLACEHOLDER_RESIDUE_RE.sub("", _strip_placeholder_noise("".join(parts)))
        return _normalize_cleanup_whitespace(self._drop_deviating_action_tags(cleaned))

    def get_expected_artifact_sequence(self) -> List[str]:
        """Returns the source's artifacts, as written and in order: what an answer must carry."""
        return [artifact.original for artifact in self.artifacts]

    def _protect(self, original: str, kind: str) -> str:
        """Registers one artifact and returns its placeholder."""
        prefix = "NWN_INLINE" if kind in _INLINE_KINDS else "NWN_TOKEN"
        core = f"{prefix}_{self._nonce}_{len(self.artifacts)}"
        placeholder = f"__{core}__"
        self.artifacts.append(PreservedArtifact(kind, original, placeholder))
        # Cores differ only in nonce and counter, so case folding is safe.
        self._originals[core.lower()] = original
        return placeholder

    def _original_for(self, match: re.Match) -> str:
        """Returns the artifact of a placeholder match, or "" for an unknown core."""
        core = next((group for group in match.groups() if group), "")
        return self._originals.get(core.lower(), "")

    def _drop_deviating_action_tags(self, text: str) -> str:
        """Drops all Start-tags when they are malformed and differ from the source's.

        Sources may legitimately carry unpaired Start-tags: modules pair
        ``</Start>`` with an engine token opener (``<CUSTOM1004>(sigh)</Start>``) or
        leave ``<StartAction>`` unclosed. Such imbalance is kept byte for byte; only
        a deviation from the source's tag inventory is malformed.
        """
        if not _has_unbalanced_action_tags(text):
            return text
        expected = Counter(
            artifact.original
            for artifact in self.artifacts
            if _INLINE_TAG_RE.fullmatch(artifact.original)
        )
        if Counter(_INLINE_TAG_RE.findall(text)) == expected:
            return text
        return _INLINE_TAG_RE.sub("", text)


def sanitize_text(text: str, preserve_tokens: bool = True) -> Tuple[str, TokenHandler]:
    """Sanitizes *text* with a new handler.

    Args:
        text: Source text.
        preserve_tokens: Protect engine tokens as well as inline markup.

    Returns:
        The sanitized text and the handler that restores it.
    """
    handler = TokenHandler(preserve_tokens=preserve_tokens)
    return handler.sanitize(text), handler
