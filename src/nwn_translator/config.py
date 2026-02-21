"""Configuration management for NWN Modules Translator."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class TranslationConfig:
    """Configuration for translation operations."""

    # API Configuration
    api_key: str = field(default_factory=lambda: os.getenv("NWN_TRANSLATE_API_KEY", ""))
    provider: str = "grok"
    model: Optional[str] = None  # Uses provider default if None

    # Language Configuration
    source_lang: str = "auto"  # Auto-detect if possible
    target_lang: str = "english"

    # File Paths
    input_file: Path = field(default_factory=Path)
    output_file: Optional[Path] = None
    translation_log: Optional[Path] = None
    
    # Advanced features
    use_context: bool = True

    # Processing Options
    temp_dir: Path = field(default_factory=lambda: Path("./temp_nwn_translate"))
    skip_cleanup: bool = False  # Keep temp files for debugging

    # Translation Options
    batch_size: int = 1  # Number of items to translate per request
    preserve_tokens: bool = True  # Preserve game tokens like <FirstName>
    translate_dialogs: bool = True
    translate_journals: bool = True
    translate_items: bool = True
    translate_creatures: bool = True
    translate_areas: bool = True
    translate_placeables: bool = True
    translate_doors: bool = True
    translate_stores: bool = True

    # Retry Configuration
    max_retries: int = 3
    retry_delay: float = 1.0  # Seconds

    # Progress Reporting
    verbose: bool = False
    quiet: bool = False

    def __post_init__(self):
        """Validate configuration after initialization."""
        self.input_file = Path(self.input_file) if isinstance(self.input_file, str) else self.input_file
        if self.output_file and isinstance(self.output_file, str):
            self.output_file = Path(self.output_file)
        if self.translation_log and isinstance(self.translation_log, str):
            self.translation_log = Path(self.translation_log)

        # Set default model based on provider if not specified
        if self.model is None:
            self.model = self._get_default_model()

        # Validate provider
        valid_providers = ["grok", "openai", "gemini", "mistral", "openrouter"]
        if self.provider not in valid_providers:
            raise ValueError(f"Invalid provider: {self.provider}. Must be one of {valid_providers}")

    def _get_default_model(self) -> str:
        """Get default model for the selected provider."""
        default_models = {
            "grok": "grok-2",
            "openai": "gpt-4o-mini",
            "gemini": "gemini-pro",
            "mistral": "mistral-medium",
            "openrouter": "openai/gpt-oss-120b",
        }
        return default_models.get(self.provider, "grok-2")

    def get_api_key(self) -> str:
        """Get API key, prompting if necessary."""
        if not self.api_key:
            raise ValueError(
                "API key is required. Set NWN_TRANSLATE_API_KEY environment variable "
                "or provide via --api-key argument."
            )
        return self.api_key


def create_output_path(input_path: Path, target_lang: str) -> Path:
    """Generate output filename based on input and target language.

    Args:
        input_path: Path to input .mod file
        target_lang: Target language name

    Returns:
        Path for output .mod file
    """
    stem = input_path.stem
    suffix = input_path.suffix
    lang_suffix = f"_{target_lang[:3].lower()}" if len(target_lang) > 3 else f"_{target_lang}"
    return input_path.parent / f"{stem}{lang_suffix}{suffix}"


# Standard NWN tokens that should be preserved
STANDARD_TOKENS = [
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
    "<CustomToken:",
]

# Translatable file extensions in NWN modules
TRANSLATABLE_TYPES = {
    ".dlg": "Dialog",
    ".jrl": "Journal",
    ".uti": "Item",
    ".utc": "Creature",
    ".are": "Area",
    ".utt": "Trigger",
    ".utp": "Placeable",
    ".utd": "Door",
    ".utm": "Store",
    ".itp": "Palette",
    ".fac": "Faction",
}
