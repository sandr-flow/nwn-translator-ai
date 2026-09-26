"""Translation manager doubles that drive the translate stage without a model."""

from __future__ import annotations

from typing import Any, List, Set, Tuple

import pytest

from nwn_translator.extractors.base import Occurrence, Translations
from nwn_translator.pipeline import stages
from nwn_translator.translators.ncs_diagnostics import new_ncs_diagnostics

#: Accepted translations and rejected occurrences of one manager.
Outcome = Tuple[Translations, Set[Occurrence]]


def stub_translation_managers(
    monkeypatch: pytest.MonkeyPatch,
    batch: Outcome = ({}, set()),
    dialogs: Outcome = ({}, set()),
) -> None:
    """Make the managers of :func:`stages.stage_translate` answer with fixed outcomes.

    The doubles write no log rows, so every row the test sees comes from the stage.

    Args:
        monkeypatch: Fixture that undoes the replacement.
        batch: Outcome of the batch manager (non-dialog items).
        dialogs: Outcome of the dialog manager.
    """
    batch_translations, batch_failed = batch
    dialog_translations, dialog_failed = dialogs

    class BatchManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.stats = {
                "items_translated": len(batch_translations),
                "errors": [],
                "ncs_diagnostics": new_ncs_diagnostics(),
            }
            self.failed_items = set(batch_failed)

        def translate_content(self, content: Any, item_progress: Any = None) -> Translations:
            return dict(batch_translations)

    class DialogManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.failed_items = set(dialog_failed)

        def translate_dialogs(
            self, dialog_files: Any, item_progress: Any = None
        ) -> Tuple[Translations, List[Any]]:
            return dict(dialog_translations), []

    monkeypatch.setattr(stages, "TranslationManager", BatchManager)
    monkeypatch.setattr(stages, "ContextualTranslationManager", DialogManager)
