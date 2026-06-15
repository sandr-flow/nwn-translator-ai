"""Token handler for preserving NWN game tokens during translation.

This module protects both classic engine tokens (for example ``<FirstName>`` or
``<CustomToken:123>``) and inline dialog markup (for example
``<StartAction>...</Start>``).  The translation pipeline uses opaque placeholders
before the LLM call, then validates the restored output against the original
artifact inventory.  When exact preservation is impossible, the handler can
clean up mismatched artifacts while keeping the translated prose.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..config import STANDARD_TOKENS


@dataclass
class TokenReplacement:
    """Represents a single token replacement operation."""

    original: str
    placeholder: str
    position: int


@dataclass(frozen=True)
class PreservedArtifact:
    """Single protected NWN artifact detected in a text."""

    kind: str
    original: str
    position: int
    structural_role: str
    placeholder: str = ""


@dataclass
class SanitizedText:
    """Text after token sanitization with metadata for restoration."""

    sanitized_text: str
    replacements: List[TokenReplacement] = field(default_factory=list)
    artifacts: List[PreservedArtifact] = field(default_factory=list)

    def add_replacement(
        self,
        original: str,
        placeholder: str,
        position: int,
        *,
        kind: str,
        structural_role: str,
    ) -> None:
        """Add one protected-artifact replacement record."""
        self.replacements.append(TokenReplacement(original, placeholder, position))
        self.artifacts.append(
            PreservedArtifact(
                kind=kind,
                original=original,
                position=position,
                structural_role=structural_role,
                placeholder=placeholder,
            )
        )


@dataclass
class TokenMismatchReport:
    """Details about preserved-artifact mismatch after restoration."""

    is_exact_match: bool
    mismatch_type: str
    expected_sequence: List[str] = field(default_factory=list)
    actual_sequence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)


@dataclass
class TokenProcessingResult:
    """Outcome of restore/validate/(optional) cleanup for one model output."""

    restored_text: str
    final_text: str
    exact_valid: bool
    used_cleanup: bool
    mismatch_report: TokenMismatchReport


class TokenHandler:
    """Handler for managing token replacement, restoration, and cleanup."""

    DIALOG_ACTION_PATTERN = re.compile(r"<<[^<>\r\n]+>>")
    # Inner body requires at least one Unicode letter (not just ASCII) so the
    # pattern still matches after restoration of translated text, e.g. "-далее-".
    DASH_ACTION_PATTERN = re.compile(r"(?<!\w)-[^-\r\n]*[^\W\d_][^-\r\n]*-(?!\w)")
    INLINE_TAG_PATTERN = re.compile(r"</?Start[A-Za-z]*>")
    ENGINE_TOKEN_PATTERN = re.compile(r"<([A-Za-z][A-Za-z0-9_]*(?:[/:][A-Za-z0-9_]+)*)>")
    UNKNOWN_ANGLE_PATTERN = re.compile(r"<[^<>\r\n]+>")
    PRESERVED_ARTIFACT_PATTERN = re.compile(
        r"<<[^<>\r\n]+>>"
        r"|(?<!\w)-[^-\r\n]*[^\W\d_][^-\r\n]*-(?!\w)"
        r"|</?Start[A-Za-z]*>"
        r"|<[A-Za-z][A-Za-z0-9_]*(?:[/:][A-Za-z0-9_]+)*>"
        r"|<[^<>\r\n]+>"
    )
    TOKEN_LIKE_FRAGMENT_PATTERN = re.compile(
        r"</?Start[A-Za-z]*>" r"|<[A-Za-z][A-Za-z0-9_]*(?:[/:][A-Za-z0-9_]+)*>" r"|<[^<>\r\n]+>"
    )
    RESTORED_ACTION_TAG_PATTERN = INLINE_TAG_PATTERN

    INLINE_PLACEHOLDER_CORE_PATTERN = r"NWN_INLINE_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
    ENGINE_PLACEHOLDER_CORE_PATTERN = r"NWN_TOKEN_[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*"
    INLINE_PLACEHOLDER_PATTERN = re.compile(
        rf"(?:__(?P<core1>{INLINE_PLACEHOLDER_CORE_PATTERN})__|\[\[(?P<core2>{INLINE_PLACEHOLDER_CORE_PATTERN})\]\]|<<\[(?P<core3>{INLINE_PLACEHOLDER_CORE_PATTERN})\]>>|<\[(?P<core4>{INLINE_PLACEHOLDER_CORE_PATTERN})\]>)"
    )
    ENGINE_PLACEHOLDER_PATTERN = re.compile(
        rf"(?:__(?P<core1>{ENGINE_PLACEHOLDER_CORE_PATTERN})__|\[\[(?P<core2>{ENGINE_PLACEHOLDER_CORE_PATTERN})\]\]|<<\[(?P<core3>{ENGINE_PLACEHOLDER_CORE_PATTERN})\]>>|<\[(?P<core4>{ENGINE_PLACEHOLDER_CORE_PATTERN})\]>)"
    )
    HELPER_PLACEHOLDER_NOISE_PATTERN = re.compile(
        rf"(?:__(?:{INLINE_PLACEHOLDER_CORE_PATTERN}|{ENGINE_PLACEHOLDER_CORE_PATTERN})__|\[\[(?:{INLINE_PLACEHOLDER_CORE_PATTERN}|{ENGINE_PLACEHOLDER_CORE_PATTERN})\]\]|<<\[(?:{INLINE_PLACEHOLDER_CORE_PATTERN}|{ENGINE_PLACEHOLDER_CORE_PATTERN})\]>>|<\[(?:{INLINE_PLACEHOLDER_CORE_PATTERN}|{ENGINE_PLACEHOLDER_CORE_PATTERN})\]>)"
    )
    HELPER_PLACEHOLDER_BARE_NOISE_PATTERN = re.compile(
        rf"[^\w\s]*(?:{INLINE_PLACEHOLDER_CORE_PATTERN}|{ENGINE_PLACEHOLDER_CORE_PATTERN})[^\w\s]*"
    )

    def __init__(self, preserve_standard_tokens: bool = True):
        """Initialize token handler."""
        self.preserve_standard_tokens = preserve_standard_tokens
        self.original_text: str = ""
        self.artifacts: List[PreservedArtifact] = []
        self.token_map: Dict[str, str] = {}
        self.action_tag_map: Dict[str, str] = {}
        self._placeholder_to_artifact: Dict[str, PreservedArtifact] = {}
        self._inline_core_map: Dict[str, str] = {}
        self._token_core_map: Dict[str, str] = {}
        self._nonce = ""
        self._artifact_counter = 0

    def sanitize(self, text: str) -> SanitizedText:
        """Replace protected artifacts in text with opaque placeholders."""
        if not text or not isinstance(text, str):
            self.clear()
            self.original_text = text or ""
            return SanitizedText(sanitized_text=text or "")

        self.clear()
        self.original_text = text
        self._nonce = self._deterministic_nonce(text)
        result = SanitizedText(sanitized_text=text)
        parts: List[str] = []
        last_end = 0

        for match in self.PRESERVED_ARTIFACT_PATTERN.finditer(text):
            original = match.group(0)
            if self.DIALOG_ACTION_PATTERN.fullmatch(original):
                inner = original[2:-2]
                opening = self._add_protected_artifact(
                    result,
                    "<<",
                    match.start(),
                    kind="dialog_action_marker",
                    structural_role="opening",
                )
                closing = self._add_protected_artifact(
                    result,
                    ">>",
                    match.end() - 2,
                    kind="dialog_action_marker",
                    structural_role="closing",
                )
                parts.append(text[last_end : match.start()])
                parts.append(opening)
                parts.append(inner)
                parts.append(closing)
                last_end = match.end()
                continue

            if self.DASH_ACTION_PATTERN.fullmatch(original):
                inner = original[1:-1]
                opening = self._add_protected_artifact(
                    result,
                    "-",
                    match.start(),
                    kind="dash_action_marker",
                    structural_role="opening",
                )
                protected_inner = self._protect_nested_artifacts(result, inner, match.start() + 1)
                closing = self._add_protected_artifact(
                    result,
                    "-",
                    match.end() - 1,
                    kind="dash_action_marker",
                    structural_role="closing",
                )
                parts.append(text[last_end : match.start()])
                parts.append(opening)
                parts.append(protected_inner)
                parts.append(closing)
                last_end = match.end()
                continue

            kind, structural_role = self._classify_artifact(original)
            if kind is None:
                continue

            placeholder = self._add_protected_artifact(
                result,
                original,
                match.start(),
                kind=kind,
                structural_role=structural_role,
            )

            parts.append(text[last_end : match.start()])
            parts.append(placeholder)
            last_end = match.end()

        parts.append(text[last_end:])
        result.sanitized_text = "".join(parts)
        return result

    def restore(self, text: str) -> str:
        """Restore protected artifacts from placeholders."""
        if not text or not isinstance(text, str):
            return text or ""

        restored = self.INLINE_PLACEHOLDER_PATTERN.sub(self._restore_inline_placeholder, text)
        restored = self.ENGINE_PLACEHOLDER_PATTERN.sub(self._restore_engine_placeholder, restored)
        restored = self.HELPER_PLACEHOLDER_NOISE_PATTERN.sub("", restored)
        restored = self.HELPER_PLACEHOLDER_BARE_NOISE_PATTERN.sub("", restored)

        if self._has_unbalanced_action_tags(restored):
            restored = self.RESTORED_ACTION_TAG_PATTERN.sub("", restored)

        return restored

    def validate_text(self, restored: str) -> TokenMismatchReport:
        """Return exact-match validation report for restored output."""
        return TokenValidator.validate_exact_artifacts(self.artifacts, restored)

    def finalize_translation(
        self,
        translated_text: str,
        *,
        allow_cleanup: bool = False,
    ) -> TokenProcessingResult:
        """Restore a model output and optionally clean mismatched artifacts."""
        restored = self.restore(translated_text)
        report = self.validate_text(restored)
        if report.is_exact_match:
            return TokenProcessingResult(
                restored_text=restored,
                final_text=restored,
                exact_valid=True,
                used_cleanup=False,
                mismatch_report=report,
            )

        if not allow_cleanup:
            return TokenProcessingResult(
                restored_text=restored,
                final_text=restored,
                exact_valid=False,
                used_cleanup=False,
                mismatch_report=report,
            )

        cleaned = self.cleanup_mismatched_artifacts(restored)
        return TokenProcessingResult(
            restored_text=restored,
            final_text=cleaned,
            exact_valid=False,
            used_cleanup=True,
            mismatch_report=report,
        )

    def cleanup_mismatched_artifacts(self, restored: str) -> str:
        """Drop helper placeholders and non-aligned token/tag-like fragments."""
        if not restored:
            return ""

        cleaned = self.HELPER_PLACEHOLDER_NOISE_PATTERN.sub("", restored)
        cleaned = self.HELPER_PLACEHOLDER_BARE_NOISE_PATTERN.sub("", cleaned)

        expected = [artifact.original for artifact in self.artifacts]
        expected_index = 0
        cursor = 0
        parts: List[str] = []

        for match in self.TOKEN_LIKE_FRAGMENT_PATTERN.finditer(cleaned):
            raw = match.group(0)
            parts.append(cleaned[cursor : match.start()])
            if expected_index < len(expected) and raw == expected[expected_index]:
                parts.append(raw)
                expected_index += 1
            cursor = match.end()

        parts.append(cleaned[cursor:])
        cleaned = "".join(parts)

        cleaned = self.HELPER_PLACEHOLDER_NOISE_PATTERN.sub("", cleaned)
        cleaned = self.HELPER_PLACEHOLDER_BARE_NOISE_PATTERN.sub("", cleaned)

        if self._has_unbalanced_action_tags(cleaned):
            cleaned = self.RESTORED_ACTION_TAG_PATTERN.sub("", cleaned)

        return self._normalize_cleanup_whitespace(cleaned)

    def get_expected_artifact_sequence(self) -> List[str]:
        """Return the exact original preserved-artifact sequence."""
        return [artifact.original for artifact in self.artifacts]

    def _add_protected_artifact(
        self,
        result: SanitizedText,
        original: str,
        position: int,
        *,
        kind: str,
        structural_role: str,
    ) -> str:
        """Register one protected artifact and return its placeholder."""
        placeholder = self._make_placeholder(kind)
        artifact = PreservedArtifact(
            kind=kind,
            original=original,
            position=position,
            structural_role=structural_role,
            placeholder=placeholder,
        )
        self.artifacts.append(artifact)
        self._placeholder_to_artifact[placeholder] = artifact
        self._store_placeholder_mapping(artifact)
        result.add_replacement(
            original,
            placeholder,
            position,
            kind=kind,
            structural_role=structural_role,
        )
        return placeholder

    def _protect_nested_artifacts(
        self, result: SanitizedText, inner: str, base_position: int
    ) -> str:
        """Protect engine tokens and inline tags nested inside an action marker.

        An action body (for example ``glances at <FirstName>`` inside
        ``-glances at <FirstName>-``) is otherwise emitted verbatim, so a nested
        token would reach the LLM unprotected and escape validation. Replace each
        classifiable fragment with a placeholder anchored at its absolute position
        in the original text, and return the rebuilt body.
        """
        parts: List[str] = []
        last_end = 0
        for match in self.TOKEN_LIKE_FRAGMENT_PATTERN.finditer(inner):
            raw = match.group(0)
            kind, structural_role = self._classify_artifact(raw)
            if kind is None:
                continue
            placeholder = self._add_protected_artifact(
                result,
                raw,
                base_position + match.start(),
                kind=kind,
                structural_role=structural_role,
            )
            parts.append(inner[last_end : match.start()])
            parts.append(placeholder)
            last_end = match.end()
        parts.append(inner[last_end:])
        return "".join(parts)

    def clear(self) -> None:
        """Clear handler state and reset placeholder counters."""
        self.original_text = ""
        self.artifacts.clear()
        self.token_map.clear()
        self.action_tag_map.clear()
        self._placeholder_to_artifact.clear()
        self._inline_core_map.clear()
        self._token_core_map.clear()
        self._nonce = ""
        self._artifact_counter = 0

    def get_token_count(self) -> int:
        """Get the number of preserved artifacts currently tracked."""
        return len(self.artifacts)

    @classmethod
    def _extract_preserved_artifacts_from_text(cls, text: str) -> List[PreservedArtifact]:
        """Extract preserved artifacts from an arbitrary restored string."""
        artifacts: List[PreservedArtifact] = []
        if not text:
            return artifacts

        for match in cls.PRESERVED_ARTIFACT_PATTERN.finditer(text):
            raw = match.group(0)
            if cls.DIALOG_ACTION_PATTERN.fullmatch(raw):
                artifacts.append(
                    PreservedArtifact(
                        kind="dialog_action_marker",
                        original="<<",
                        position=match.start(),
                        structural_role="opening",
                    )
                )
                artifacts.append(
                    PreservedArtifact(
                        kind="dialog_action_marker",
                        original=">>",
                        position=match.end() - 2,
                        structural_role="closing",
                    )
                )
                continue

            if cls.DASH_ACTION_PATTERN.fullmatch(raw):
                artifacts.append(
                    PreservedArtifact(
                        kind="dash_action_marker",
                        original="-",
                        position=match.start(),
                        structural_role="opening",
                    )
                )
                inner = raw[1:-1]
                for inner_match in cls.TOKEN_LIKE_FRAGMENT_PATTERN.finditer(inner):
                    inner_raw = inner_match.group(0)
                    inner_kind, inner_role = cls._classify_artifact_static(inner_raw)
                    if inner_kind is None:
                        continue
                    artifacts.append(
                        PreservedArtifact(
                            kind=inner_kind,
                            original=inner_raw,
                            position=match.start() + 1 + inner_match.start(),
                            structural_role=inner_role,
                        )
                    )
                artifacts.append(
                    PreservedArtifact(
                        kind="dash_action_marker",
                        original="-",
                        position=match.end() - 1,
                        structural_role="closing",
                    )
                )
                continue

            kind, structural_role = cls._classify_artifact_static(raw)
            if kind is None:
                continue
            artifacts.append(
                PreservedArtifact(
                    kind=kind,
                    original=raw,
                    position=match.start(),
                    structural_role=structural_role,
                )
            )
        return artifacts

    @classmethod
    def _has_unbalanced_action_tags(cls, text: str) -> bool:
        """Return True when ``<Start...>``/``</Start>`` tags are malformed."""
        depth = 0
        for tag in cls.RESTORED_ACTION_TAG_PATTERN.findall(text):
            if tag.startswith("</"):
                depth -= 1
            else:
                depth += 1
            if depth < 0:
                return True
        return depth != 0

    @staticmethod
    def _normalize_cleanup_whitespace(text: str) -> str:
        """Minimally normalize whitespace after artifact removal."""
        normalized = re.sub(r"[ \t]+\n", "\n", text)
        normalized = re.sub(r"\n[ \t]+", "\n", normalized)
        normalized = re.sub(r"[ \t]{2,}", " ", normalized)
        normalized = re.sub(r" +([,.;:!?])", r"\1", normalized)
        normalized = re.sub(r"\( ", "(", normalized)
        normalized = re.sub(r" \)", ")", normalized)
        return normalized.strip()

    @staticmethod
    def _deterministic_nonce(text: str) -> str:
        """Derive the placeholder nonce from the source text.

        Identical input text yields identical placeholders, so two equal strings
        sanitize to the same form and share one session-cache / dedup key (and one
        paid LLM call). Different texts get different nonces. Per-artifact
        uniqueness within a single string is handled by ``_artifact_counter``.
        """
        return hashlib.blake2s(text.encode("utf-8"), digest_size=4).hexdigest()

    def _make_placeholder(self, kind: str) -> str:
        """Build one opaque placeholder for a preserved artifact."""
        prefix = (
            "NWN_INLINE"
            if kind in {"inline_tag", "dialog_action_marker", "dash_action_marker"}
            else "NWN_TOKEN"
        )
        core = f"{prefix}_{self._nonce}_{self._artifact_counter}"
        self._artifact_counter += 1
        return f"__{core}__"

    def _store_placeholder_mapping(self, artifact: PreservedArtifact) -> None:
        """Store restoration mapping for one protected artifact."""
        core = artifact.placeholder[2:-2]
        if artifact.kind in {"inline_tag", "dialog_action_marker", "dash_action_marker"}:
            self.action_tag_map[core] = artifact.original
            self._inline_core_map[core] = artifact.original
        else:
            self.token_map[artifact.placeholder] = artifact.original
            self._token_core_map[core] = artifact.original

    def _restore_inline_placeholder(self, match: re.Match) -> str:
        """Restore one inline-tag placeholder or drop unknown helper artifacts."""
        core = next((group for group in match.groups() if group), None)
        if not core:
            return ""
        return self._inline_core_map.get(core, "")

    def _restore_engine_placeholder(self, match: re.Match) -> str:
        """Restore one engine-token placeholder or drop unknown helper artifacts."""
        core = next((group for group in match.groups() if group), None)
        if not core:
            return ""
        return self._token_core_map.get(core, "")

    def _classify_artifact(self, raw: str) -> Tuple[Optional[str], str]:
        """Classify one raw artifact candidate from the original text."""
        return self._classify_artifact_static(
            raw, preserve_standard_tokens=self.preserve_standard_tokens
        )

    @classmethod
    def _classify_artifact_static(
        cls,
        raw: str,
        *,
        preserve_standard_tokens: bool = True,
    ) -> Tuple[Optional[str], str]:
        """Classify one raw artifact candidate from any text."""
        if cls.INLINE_TAG_PATTERN.fullmatch(raw):
            role = "closing" if raw.startswith("</") else "opening"
            return "inline_tag", role

        token_match = cls.ENGINE_TOKEN_PATTERN.fullmatch(raw)
        if token_match:
            token_name = token_match.group(1)
            if cls._should_preserve_token_static(
                token_name,
                preserve_standard_tokens=preserve_standard_tokens,
            ):
                return "engine_token", "token"
            return None, ""

        if cls.UNKNOWN_ANGLE_PATTERN.fullmatch(raw):
            return "angle_fragment", "token"

        return None, ""

    def _should_preserve_token(self, token_name: str) -> bool:
        """Determine if a token should be preserved."""
        return self._should_preserve_token_static(
            token_name,
            preserve_standard_tokens=self.preserve_standard_tokens,
        )

    @staticmethod
    def _should_preserve_token_static(
        token_name: str,
        *,
        preserve_standard_tokens: bool = True,
    ) -> bool:
        """Determine if a token-like candidate should be preserved."""
        if not preserve_standard_tokens:
            return False

        if not token_name:
            return False

        full_token = f"<{token_name}>"
        if full_token in STANDARD_TOKENS:
            return True

        if token_name.startswith("CustomToken:"):
            return True

        if "/" in token_name or ":" in token_name:
            return True

        # Preserve any safe identifier-like angle token, not only the hardcoded whitelist.
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:[/:][A-Za-z0-9_]+)*", token_name))


class TokenValidator:
    """Validator for checking exact preserved-artifact preservation."""

    @staticmethod
    def validate_restoration(original: str, restored: str) -> bool:
        """Validate exact preserved-artifact sequence equality."""
        report = TokenValidator.validate_exact_texts(original, restored)
        return report.is_exact_match

    @staticmethod
    def validate_exact_texts(original: str, restored: str) -> TokenMismatchReport:
        """Compare preserved artifacts between two arbitrary texts."""
        expected = TokenHandler._extract_preserved_artifacts_from_text(original)
        return TokenValidator.validate_exact_artifacts(expected, restored)

    @staticmethod
    def validate_exact_artifacts(
        expected_artifacts: List[PreservedArtifact],
        restored: str,
    ) -> TokenMismatchReport:
        """Compare expected preserved artifacts against restored output."""
        actual_artifacts = TokenHandler._extract_preserved_artifacts_from_text(restored)
        expected_sequence = [artifact.original for artifact in expected_artifacts]
        actual_sequence = [artifact.original for artifact in actual_artifacts]

        if expected_sequence == actual_sequence:
            return TokenMismatchReport(
                is_exact_match=True,
                mismatch_type="exact_match",
                expected_sequence=expected_sequence,
                actual_sequence=actual_sequence,
            )

        missing, extra = TokenValidator.find_token_mismatches_from_sequences(
            expected_sequence,
            actual_sequence,
        )

        mismatch_type = "value_mismatch"
        if len(expected_sequence) != len(actual_sequence):
            mismatch_type = "count_mismatch"
        elif sorted(expected_sequence) == sorted(actual_sequence):
            mismatch_type = "order_mismatch"

        return TokenMismatchReport(
            is_exact_match=False,
            mismatch_type=mismatch_type,
            expected_sequence=expected_sequence,
            actual_sequence=actual_sequence,
            missing=missing,
            extra=extra,
        )

    @staticmethod
    def find_token_mismatches(original: str, restored: str) -> Tuple[List[str], List[str]]:
        """Find missing/extra preserved artifacts between two texts."""
        report = TokenValidator.validate_exact_texts(original, restored)
        return report.missing, report.extra

    @staticmethod
    def find_token_mismatches_from_sequences(
        expected_sequence: List[str],
        actual_sequence: List[str],
    ) -> Tuple[List[str], List[str]]:
        """Find left-to-right missing and extra artifacts preserving duplicates."""
        expected = list(expected_sequence)
        actual = list(actual_sequence)

        missing: List[str] = []
        extra: List[str] = []
        expected_index = 0
        actual_index = 0

        while expected_index < len(expected) and actual_index < len(actual):
            if expected[expected_index] == actual[actual_index]:
                expected_index += 1
                actual_index += 1
                continue

            if actual[actual_index] in expected[expected_index + 1 :]:
                missing.append(expected[expected_index])
                expected_index += 1
            else:
                extra.append(actual[actual_index])
                actual_index += 1

        missing.extend(expected[expected_index:])
        extra.extend(actual[actual_index:])
        return missing, extra

    @staticmethod
    def extract_all_tokens(text: str) -> List[str]:
        """Extract engine-token names from text (legacy helper)."""
        tokens = [
            artifact.original[1:-1]
            for artifact in TokenHandler._extract_preserved_artifacts_from_text(text)
            if artifact.kind == "engine_token"
        ]
        return sorted(set(tokens))


def sanitize_text(text: str, preserve_tokens: bool = True) -> Tuple[str, TokenHandler]:
    """Sanitize text by replacing preserved artifacts with placeholders."""
    handler = TokenHandler(preserve_standard_tokens=preserve_tokens)
    result = handler.sanitize(text)
    return result.sanitized_text, handler


def restore_text(text: str, handler: TokenHandler) -> str:
    """Restore preserved artifacts from placeholders."""
    return handler.restore(text)
