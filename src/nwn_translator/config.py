"""Configuration management for NWN Modules Translator."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .translation_logging import TranslationLogWriter


# Callback: phase, current index (0-based), total count, optional message (e.g. filename).
ProgressCallback = Callable[[str, int, int, Optional[str]], None]

# Model generation parameters
TRANSLATION_TEMPERATURE: float = 0.6
TRANSLATION_MAX_TOKENS: int = 16384
GLOSSARY_TEMPERATURE: float = 0.3
GLOSSARY_FALLBACK_TEMPERATURE: float = 0.2
GLOSSARY_MAX_TOKENS: int = 8192


def _glossary_llm_timeout() -> float:
    """Timeout (seconds) for a single LLM glossary call.

    Override with ``NWN_GLOSSARY_LLM_TIMEOUT`` env var (min 30s).
    """
    raw = os.getenv("NWN_GLOSSARY_LLM_TIMEOUT", "300").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 300.0


def _glossary_run_timeout() -> float:
    """Overall timeout (seconds) for the run_async wrapper around a glossary LLM call.

    Override with ``NWN_GLOSSARY_RUN_TIMEOUT`` env var (min 60s).
    """
    raw = os.getenv("NWN_GLOSSARY_RUN_TIMEOUT", "360").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 360.0


GLOSSARY_LLM_TIMEOUT: float = _glossary_llm_timeout()
GLOSSARY_RUN_TIMEOUT: float = _glossary_run_timeout()

# OpenRouter Chat API ``reasoning.effort`` (see OpenRouter schema).
OPENROUTER_REASONING_EFFORT_VALUES = frozenset(
    {"xhigh", "high", "medium", "low", "minimal", "none"}
)


def parse_reasoning_effort(raw: Optional[str]) -> Optional[str]:
    """Normalize OpenRouter ``reasoning.effort`` or return ``None`` when disabled.

    Raises:
        ValueError: If *raw* is non-empty but not a known effort level.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if s not in OPENROUTER_REASONING_EFFORT_VALUES:
        raise ValueError(
            f"Invalid reasoning_effort {raw!r}; expected one of "
            f"{sorted(OPENROUTER_REASONING_EFFORT_VALUES)}"
        )
    return s


def max_concurrent_from_environment() -> int:
    """Max parallel OpenRouter HTTP requests (asyncio + semaphore, not OS threads).

    Override with environment variable ``NWN_TRANSLATE_MAX_CONCURRENT`` (integer, min 1).
    Sensible range: 10–12 if you hit HTTP 429; 15–20 when your OpenRouter tier allows it.
    """
    raw = os.getenv("NWN_TRANSLATE_MAX_CONCURRENT", "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


@dataclass
class TranslationConfig:
    """Configuration for translation operations."""

    # API Configuration (OpenRouter only)
    api_key: str = field(default_factory=lambda: os.getenv("NWN_TRANSLATE_API_KEY", ""))
    model: Optional[str] = None  # Uses OpenRouter default if None

    # Language Configuration
    source_lang: str = "auto"  # Auto-detect if possible
    target_lang: str = "english"

    # File Paths
    input_file: Path = field(default_factory=Path)
    output_file: Optional[Path] = None
    translation_log: Optional[Path] = None
    #: Optional injected writer (e.g. for web/DB). If set, used instead of ``translation_log`` file.
    translation_log_writer: Optional[TranslationLogWriter] = None

    # Advanced features
    use_context: bool = True
    tlk_file: Optional[Path] = None  # Path to dialog.tlk for resolving StrRef names
    player_gender: str = "male"  # "male" or "female" — affects grammatical gender in translations

    # Processing Options
    temp_dir: Path = field(default_factory=lambda: Path("./temp_nwn_translate"))
    skip_cleanup: bool = False  # Keep temp files for debugging

    # Translation Options
    #: Max concurrent OpenRouter requests for line-by-line translation (async).
    #: Default: :func:`max_concurrent_from_environment` (``NWN_TRANSLATE_MAX_CONCURRENT`` or 12).
    max_concurrent_requests: int = field(default_factory=max_concurrent_from_environment)
    preserve_tokens: bool = True  # Preserve game tokens like <FirstName>

    #: If True, skip the LLM gate for NCS strings (translate all extractor-approved items).
    skip_ncs_llm_gate: bool = False

    #: Optional OpenRouter ``reasoning.effort`` (``None`` = omit parameter, same as before).
    reasoning_effort: Optional[str] = None

    # Progress Reporting
    verbose: bool = False
    quiet: bool = False
    #: If set, tqdm is not used; caller receives progress (for SSE/WebSocket, etc.).
    progress_callback: Optional[ProgressCallback] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        self.input_file = Path(self.input_file) if isinstance(self.input_file, str) else self.input_file
        if self.output_file and isinstance(self.output_file, str):
            self.output_file = Path(self.output_file)
        if self.translation_log and isinstance(self.translation_log, str):
            self.translation_log = Path(self.translation_log)

        if self.model is None:
            from .ai_providers.openrouter_provider import OpenRouterProvider

            self.model = OpenRouterProvider.DEFAULT_MODEL

        self.reasoning_effort = parse_reasoning_effort(self.reasoning_effort)

    def get_api_key(self) -> str:
        """Get API key, prompting if necessary."""
        if not self.api_key:
            raise ValueError(
                "API key is required. Set NWN_TRANSLATE_API_KEY environment variable "
                "or provide via --api-key argument."
            )
        return self.api_key


# GFF/NCS injection encodes player-visible strings with a Windows code page chosen
# from the target language (see :func:`module_string_encoding_for_target_lang` and
# ``gff_patcher`` / ``ncs_patcher``).
#
# **CJK** cannot be represented in these single-byte pages; those tags are blocked in
# the web UI / API.
GAME_INCOMPATIBLE_TARGET_LANGS = frozenset({"chinese", "japanese", "korean"})

# Language slug -> Python codec (must stay in sync with ``gff_patcher`` allow-list).
_LANG_TO_WINDOWS_ENCODING: dict[str, str] = {
    "russian": "cp1251",
    "ukrainian": "cp1251",
    "polish": "cp1250",
    "czech": "cp1250",
    "hungarian": "cp1250",
    "romanian": "cp1250",
    "german": "cp1252",
    "french": "cp1252",
    "spanish": "cp1252",
    "italian": "cp1252",
    "portuguese": "cp1252",
    "dutch": "cp1252",
    "english": "cp1252",
    "turkish": "cp1254",
}


def target_lang_supported_for_nwn_injection(target_lang: str) -> bool:
    """Whether *target_lang* can be represented after binary inject into module resources."""
    key = (target_lang or "").strip().lower()
    return key not in GAME_INCOMPATIBLE_TARGET_LANGS


def module_string_encoding_for_target_lang(target_lang: Optional[str]) -> str:
    """Windows code page name used for GFF/NCS string bytes for *target_lang*."""
    key = (target_lang or "").strip().lower()
    if not key:
        return "cp1251"
    return _LANG_TO_WINDOWS_ENCODING.get(key, "cp1252")


def sanitized_mod_stem(stem: str) -> str:
    """Stem for translated module files: underscores are not allowed (use hyphens)."""
    return stem.replace("_", "-")


def lang_suffix(target_lang: str) -> str:
    """Build a short language tag for output filenames (hyphen-separated, no underscores).

    Args:
        target_lang: Target language name (e.g. ``"russian"``).

    Returns:
        Tag string like ``"-rus"`` or ``"-de"``.
    """
    tag = target_lang[:3].lower() if len(target_lang) > 3 else target_lang.lower()
    return f"-{tag}"


def create_output_path(input_path: Path, target_lang: str) -> Path:
    """Generate output filename based on input and target language.

    Args:
        input_path: Path to input .mod file
        target_lang: Target language name

    Returns:
        Path for output .mod file
    """
    stem = sanitized_mod_stem(input_path.stem)
    suffix = input_path.suffix
    return input_path.parent / f"{stem}{lang_suffix(target_lang)}{suffix}"


# Standard NWN tokens that should be preserved (frozenset for O(1) membership)
STANDARD_TOKENS = frozenset(
    {
        "<FirstName>",
        "<LastName>",
        "<Class>",
        "<Race>",
        "<Gender>",
        "<HisHer>",
        "<HeShe>",
        "<HimHer>",
        "<BoyGirl>",
        "<BrotherSister>",
        "<SirMadam>",
        "<LadLass>",
        "<MasterMistress>",
        "<LordLady>",
        "<Possessive>",
        "<Subject>",
        "<Target>",
    }
)

# Translatable file extensions in NWN modules (only types with extractors)
TRANSLATABLE_TYPES = {
    ".dlg": "Dialog",
    ".jrl": "Journal",
    ".uti": "Item",
    ".utc": "Creature",
    ".are": "Area",
    ".utt": "Trigger",
    ".utp": "Placeable",
    ".utd": "Door",
    ".ute": "Encounter",
    ".utm": "Store",
    ".git": "Area Instance",
    ".ifo": "Module Info",
    ".ncs": "Script",
}
