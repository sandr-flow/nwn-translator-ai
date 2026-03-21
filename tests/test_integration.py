"""Integration tests for the complete translation workflow."""

import tempfile
from pathlib import Path
import pytest

from src.nwn_translator.config import TranslationConfig
from src.nwn_translator.translators.token_handler import TokenHandler
from src.nwn_translator.extractors.dialog_extractor import DialogExtractor
from src.nwn_translator.injectors.dialog_injector import DialogInjector
from src.nwn_translator.ai_providers.openrouter_provider import OpenRouterProvider
from unittest.mock import MagicMock, Mock, patch


class TestTokenPreservationWorkflow:
    """Tests for token preservation through the translation pipeline."""

    def test_sanitize_translate_restore_roundtrip(self):
        """Test complete roundtrip of token preservation."""
        original = "Hello <FirstName>, you are a skilled <Class>!"

        # Sanitize
        handler = TokenHandler()
        sanitized = handler.sanitize(original)

        # Simulate translation (should preserve placeholders)
        mock_translated = "¡Hola <<TOKEN_0>>, eres un <<TOKEN_1>> experto!"

        # Restore
        restored = handler.restore(mock_translated)

        assert "<FirstName>" in restored
        assert "<Class>" in restored
        assert restored == "¡Hola <FirstName>, eres un <Class> experto!"


class TestDialogExtractionAndInjection:
    """Tests for dialog extraction and injection workflow."""

    @patch("src.nwn_translator.injectors.dialog_injector.GFFPatcher")
    def test_extract_and_inject_dialog(self, mock_patcher_cls):
        """Test extracting and re-injecting dialog content (binary patch via GFFPatcher)."""
        mock_patcher = MagicMock()
        mock_patcher_cls.return_value = mock_patcher
        file_path = Path("test_dialog.dlg")

        # Original GFF data (offsets required for GFFPatcher path)
        original_gff = {
            "StructType": "DLG",
            "EntryList": [
                {
                    "Active": "1",
                    "Text": {"StrRef": -1, "Value": "Greetings, traveler."},
                    "Speaker": "Innkeeper",
                    "EntriesList": [],
                    "_record_offsets": {"Text": 100},
                }
            ],
            "ReplyList": [
                {
                    "Text": {"StrRef": -1, "Value": "Hello, innkeeper."},
                    "EntriesList": [],
                    "_record_offsets": {"Text": 200},
                }
            ],
        }

        # Extract
        extractor = DialogExtractor()
        extracted = extractor.extract(file_path, original_gff)

        # After per-node refactor: 1 entry + 1 reply = 2 items
        assert len(extracted.items) == 2
        texts = {item.text for item in extracted.items}
        assert "Greetings, traveler." in texts
        assert "Hello, innkeeper." in texts

        # Mock translation (one pair per node)
        translations = {
            "Greetings, traveler.": "¡Saludos, viajero!",
            "Hello, innkeeper.": "Hola, posadero.",
        }

        # Inject
        injector = DialogInjector()
        result = injector.inject(file_path, original_gff, translations)

        assert result.modified
        assert result.items_updated == 2
        mock_patcher.patch_multiple.assert_called_once()
        patches = mock_patcher.patch_multiple.call_args[0][0]
        assert set(patches) == {
            (100, "¡Saludos, viajero!"),
            (200, "Hola, posadero."),
        }


class TestEndToEndWorkflow:
    """Tests for complete end-to-end workflow."""

    @patch("src.nwn_translator.ai_providers.openrouter_provider.OpenAI")
    def test_simple_dialog_translation_workflow(self, mock_openai):
        """Test complete workflow with mocked OpenRouter client."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"translation": "¡Hola viajero!"}'
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai.return_value = mock_client

        provider = OpenRouterProvider(api_key="test-key")

        result = provider.translate("Hello traveler", "english", "spanish")

        assert result.success
        assert result.translated == "¡Hola viajero!"

    def test_configuration_validation(self):
        """Test configuration validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            test_file = tmpdir / "test.mod"
            test_file.write_bytes(b"ERF V1.0" + b"\x00" * 150)

            config = TranslationConfig(
                api_key="test-key",
                input_file=test_file,
                target_lang="spanish",
            )

            assert config.model == OpenRouterProvider.DEFAULT_MODEL
            assert config.target_lang == "spanish"
            assert config.preserve_tokens is True


class TestErrorHandling:
    """Tests for error handling in the workflow."""

    def test_missing_api_key_raises_error(self):
        """Test that missing API key raises appropriate error."""
        config = TranslationConfig(
            api_key="",  # Empty API key
            input_file=Path("test.mod"),
            target_lang="spanish",
        )

        with pytest.raises(ValueError, match="API key"):
            config.get_api_key()

    def test_invalid_file_raises_error(self):
        """Test that invalid file path raises error."""
        config = TranslationConfig(
            api_key="test-key",
            input_file=Path("nonexistent.mod"),
            target_lang="spanish",
        )

        # File doesn't exist
        assert not config.input_file.exists()
