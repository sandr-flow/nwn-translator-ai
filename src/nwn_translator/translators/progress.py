"""Progress counter that both translation managers report to."""

from typing import Optional, Protocol


class ItemProgress(Protocol):
    """Progress counter of a run, bumped by both translation managers."""

    def bump(self, by: int = 1, filename: Optional[str] = None) -> None:
        """Counts *by* finished items of *filename*.

        Args:
            by: Items finished.
            filename: Resource the items belong to.
        """
