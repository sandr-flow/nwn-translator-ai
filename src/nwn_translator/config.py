"""Run configuration, model defaults and module text encodings.

Holds the LLM request constants, the environment overrides, :class:`TranslationConfig`,
the target-language code-page tables used by injection, and output file naming.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Tuple, TypeVar

from .translation_logging import TranslationLogWriter

#: Callback: phase, current index (0-based), total count, optional message (e.g. filename).
ProgressCallback = Callable[[str, int, int, Optional[str]], None]

_Number = TypeVar("_Number", int, float)


def _env_number(
    name: str, default: _Number, minimum: _Number, parse: Callable[[str], _Number]
) -> _Number:
    """Reads a numeric environment override, clamped to a lower bound.

    Args:
        name: Environment variable name.
        default: Value when the variable is unset or does not parse.
        minimum: Lower bound of a parsed value.
        parse: ``int`` or ``float``.

    Returns:
        ``max(minimum, parse(value))``, or *default*.
    """
    try:
        return max(minimum, parse(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


#: Model used when neither the web request nor :class:`TranslationConfig` names one.
DEFAULT_MODEL = "google/gemini-3.8-flash"

# Model generation parameters.
# max_tokens budgets include hidden reasoning tokens on reasoning-by-default
# models (e.g. DeepSeek v4): keep enough headroom that a long think does not
# truncate the actual answer.
TRANSLATION_TEMPERATURE: float = 0.6
TRANSLATION_MAX_TOKENS: int = 32768
GLOSSARY_TEMPERATURE: float = 0.3
GLOSSARY_MAX_TOKENS: int = 16384
#: The NCS gate must decide conservatively, so it samples close to greedy.
NCS_GATE_TEMPERATURE: float = 0.15
#: Token budget of each NCS gate attempt: a verdict map that does not parse (usually
#: a truncated one) is requested once more with twice the budget before the batch is split.
NCS_GATE_MAX_TOKENS: Tuple[int, ...] = (8192, 16384)

#: Timeout (s) of one glossary, curator or entity-extraction LLM call. It is also
#: the per-batch share of the overall deadline of a curation run (times the batch
#: count, uncapped; batches unfinished by then keep their deterministic decisions);
#: ``NWN_GLOSSARY_LLM_TIMEOUT`` overrides it (min 30).
GLOSSARY_LLM_TIMEOUT: float = _env_number("NWN_GLOSSARY_LLM_TIMEOUT", 300.0, 30.0, float)
#: Per-batch share (s) of the overall deadline of a glossary or entity-extraction run
#: (times the batch count, capped at ``llm_batches.RUN_TIMEOUT_CAP``; batches
#: unfinished by then are dropped); ``NWN_GLOSSARY_RUN_TIMEOUT`` overrides it (min 60).
GLOSSARY_RUN_TIMEOUT: float = _env_number("NWN_GLOSSARY_RUN_TIMEOUT", 360.0, 60.0, float)

#: OpenRouter ``reasoning.effort`` values, lowest first.
REASONING_EFFORTS: Tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def parse_reasoning_effort(raw: Optional[str]) -> Optional[str]:
    """Normalizes a requested ``reasoning.effort``.

    Args:
        raw: Effort name in any letter case; ``None`` or blank means "not requested".

    Returns:
        The lower-case effort, or ``None`` when *raw* is ``None`` or blank.

    Raises:
        ValueError: If *raw* is non-empty but not one of :data:`REASONING_EFFORTS`.
    """
    effort = "" if raw is None else str(raw).strip().lower()
    if effort and effort not in REASONING_EFFORTS:
        raise ValueError(
            f"Invalid reasoning_effort {raw!r}; expected one of {sorted(REASONING_EFFORTS)}"
        )
    return effort or None


#: Emit a ``cache_control: ephemeral`` breakpoint between the stable and variable halves
#: of the system prompt. Anthropic, Gemini and Grok cache at the breakpoint; OpenAI and
#: DeepSeek cache prefixes automatically and ignore it. ``NWN_TRANSLATE_PROMPT_CACHE=0``
#: turns it off for a gateway that rejects the extra field.
PROMPT_CACHE_BREAKPOINTS_ENABLED: bool = os.getenv(
    "NWN_TRANSLATE_PROMPT_CACHE", "1"
).strip().lower() not in {"0", "false", "no", "off"}


def max_concurrent_from_environment() -> int:
    """Returns the number of concurrent model requests (asyncio slots, not threads).

    ``NWN_TRANSLATE_MAX_CONCURRENT`` overrides the default 12 (min 1): 10-12 suits
    a gateway that answers HTTP 429, 15-20 an account tier that allows more.

    Returns:
        The configured request concurrency.
    """
    return _env_number("NWN_TRANSLATE_MAX_CONCURRENT", 12, 1, int)


class TranslationCancelled(Exception):
    """Raised when ``TranslationConfig.cancel_check`` signals cancellation."""


@dataclass
class TranslationConfig:
    """Settings of one translation run.

    Attributes:
        api_key: OpenRouter (``sk-or-...``) or POLZA.AI (``pza...``) key; defaults to
            ``NWN_TRANSLATE_API_KEY``.
        model: Model slug; ``None`` becomes :data:`DEFAULT_MODEL`.
        source_lang: Source language name, or ``"auto"`` to detect module encodings.
        target_lang: Target language name.
        input_file: Module or archive to translate.
        output_file: Output path; derived from *input_file* when ``None``.
        translation_log: JSONL translation log path, or ``None`` for no log.
        metrics_output: Request-metrics JSON path, or ``None``.
        translation_log_writer: Injected log writer (web database); wins over
            *translation_log*.
        use_context: Build world context, entities and glossary, and translate
            dialogs as whole conversations.
        player_gender: ``"male"`` or ``"female"``; grammatical gender used when the
            player is addressed or described.
        temp_dir: Working directory for extracted resources.
        skip_cleanup: Keep *temp_dir* after the run (debugging).
        max_concurrent_requests: Concurrent model requests of every phase; defaults
            to :func:`max_concurrent_from_environment`.
        preserve_tokens: Protect game tokens such as ``<FirstName>`` from the model.
        skip_ncs_llm_gate: Skip model review for bytecode-proven display strings and
            reject unproven NCS candidates.
        reasoning_effort: Requested ``reasoning.effort``. ``None`` sends the lowest
            effort a catalog-known reasoning model accepts (omitting the field would
            enable the model's default effort) and omits the field for other models.
        verbose: Verbose progress output.
        quiet: No progress bars.
        progress_callback: Receives ``(phase, current, total, message)`` progress
            events instead of tqdm bars (the web task manager stores them on the task).
        cancel_check: Polled at safe points (between batches, phases and dialog files);
            returning ``True`` raises :class:`TranslationCancelled`. In-flight requests
            are not aborted; their results are discarded.
    """

    api_key: str = field(default_factory=lambda: os.getenv("NWN_TRANSLATE_API_KEY", ""))
    model: Optional[str] = None

    source_lang: str = "auto"
    target_lang: str = "english"

    input_file: Path = field(default_factory=Path)
    output_file: Optional[Path] = None
    translation_log: Optional[Path] = None
    metrics_output: Optional[Path] = None
    translation_log_writer: Optional[TranslationLogWriter] = None

    use_context: bool = True
    player_gender: str = "male"

    temp_dir: Path = field(default_factory=lambda: Path("./temp_nwn_translate"))
    skip_cleanup: bool = False

    max_concurrent_requests: int = field(default_factory=max_concurrent_from_environment)
    preserve_tokens: bool = True
    skip_ncs_llm_gate: bool = False
    reasoning_effort: Optional[str] = None

    verbose: bool = False
    quiet: bool = False
    progress_callback: Optional[ProgressCallback] = None
    cancel_check: Optional[Callable[[], bool]] = None

    def __post_init__(self):
        """Coerces path strings, applies the default model and normalizes the effort.

        Raises:
            ValueError: If ``reasoning_effort`` is not a known effort.
        """
        if isinstance(self.input_file, str):
            self.input_file = Path(self.input_file)
        for name in ("output_file", "translation_log", "metrics_output"):
            value = getattr(self, name)
            if value and isinstance(value, str):
                setattr(self, name, Path(value))
        if self.model is None:
            self.model = DEFAULT_MODEL
        self.reasoning_effort = parse_reasoning_effort(self.reasoning_effort)

    def raise_if_cancelled(self) -> None:
        """Stops the run when :attr:`cancel_check` asks for it.

        Raises:
            TranslationCancelled: If ``cancel_check`` returns ``True``.
        """
        if self.cancel_check is not None and self.cancel_check():
            raise TranslationCancelled("Translation cancelled by user")

    def get_api_key(self) -> str:
        """Returns the API key.

        Returns:
            The configured key.

        Raises:
            ValueError: If no key is configured.
        """
        if not self.api_key:
            raise ValueError(
                "API key is required. Set the NWN_TRANSLATE_API_KEY environment variable "
                "or pass api_key."
            )
        return self.api_key


#: Languages the game cannot display: injection writes a single-byte Windows code
#: page, CJK does not fit one, and NWN:EE offers only cp1250/cp1251/cp1252 (not
#: Turkish cp1254). The web API refuses them.
GAME_INCOMPATIBLE_TARGET_LANGS = frozenset({"chinese", "japanese", "korean", "turkish"})

#: Language -> code page of injected strings; a test keeps the values equal to
#: ``formats.text_codec.MODULE_ENCODINGS``, the pages the patchers accept.
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
}


def target_lang_supported_for_nwn_injection(target_lang: str) -> bool:
    """Tells whether the game can display *target_lang* after injection.

    Args:
        target_lang: Target language name.

    Returns:
        ``False`` for languages outside the single-byte code pages NWN:EE offers.
    """
    key = (target_lang or "").strip().lower()
    return key not in GAME_INCOMPATIBLE_TARGET_LANGS


def module_string_encoding_for_target_lang(target_lang: Optional[str]) -> str:
    """Returns the Windows code page for GFF/NCS string bytes in *target_lang*.

    Args:
        target_lang: Target language name.

    Returns:
        ``cp1251`` for an empty name, the table entry for a known language,
        ``cp1252`` otherwise.
    """
    key = (target_lang or "").strip().lower()
    if not key:
        return "cp1251"
    return _LANG_TO_WINDOWS_ENCODING.get(key, "cp1252")


def source_string_encoding(source_lang: Optional[str]) -> Optional[str]:
    """Returns the Windows code page for decoding module strings of *source_lang*.

    Args:
        source_lang: Source language name or ``"auto"``.

    Returns:
        The code page, or ``None`` for ``"auto"``, empty or unknown languages; readers
        then detect the encoding (see ``decode_module_text``).
    """
    key = (source_lang or "").strip().lower()
    if not key or key == "auto":
        return None
    return _LANG_TO_WINDOWS_ENCODING.get(key)


def create_output_path(
    input_path: Path,
    target_lang: str,
    output_dir: Optional[Path] = None,
) -> Path:
    """Derives the translated module's path from the input path and target language.

    Args:
        input_path: Input module path.
        target_lang: Target language name.
        output_dir: Directory for the output; the input's directory when ``None``.

    Returns:
        ``<dir>/<stem, underscores as hyphens>-<first three letters of the language><ext>``,
        e.g. ``my-mod-rus.mod`` for ``my_mod.mod`` in Russian.
    """
    stem = input_path.stem.replace("_", "-")
    parent = Path(output_dir) if output_dir is not None else input_path.parent
    return parent / f"{stem}-{target_lang[:3].lower()}{input_path.suffix}"
