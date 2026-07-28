"""Translation cache with trie-backed longest-prefix lookup for journal chains."""

from __future__ import annotations

from typing import Dict, Iterator, Optional, Tuple


def _is_word_char(ch: str) -> bool:
    """Return True for characters that may not be split across a prefix boundary."""
    return ch.isalnum() or ch == "_"


class _TrieNode:
    __slots__ = ("children", "key_at_end", "value_at_end")

    def __init__(self) -> None:
        self.children: Dict[str, _TrieNode] = {}
        self.key_at_end: Optional[str] = None
        self.value_at_end: Optional[str] = None


def _trie_insert(root: _TrieNode, key: str, translation: str) -> None:
    node = root
    for ch in key:
        node = node.children.setdefault(ch, _TrieNode())
    node.key_at_end = key
    node.value_at_end = translation


class PrefixAwareTranslationCache:
    """``sanitized -> translated`` map with O(len(query)) longest-prefix search."""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}
        self._root = _TrieNode()

    def __setitem__(self, key: str, value: str) -> None:
        self._data[key] = value
        _trie_insert(self._root, key, value)

    def set_exact(self, key: str, value: str) -> None:
        """Store an exact-match-only entry, excluded from prefix lookups.

        Glossary seeds are terminology, not journal-chain bases, so they must
        not seed longest-prefix matches; they live in the exact-match map only
        and never enter the trie.
        """
        self._data[key] = value

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def longest_prefix_match(self, sanitized: str, min_len: int) -> Optional[Tuple[str, str]]:
        """Longest cached key that is a prefix of *sanitized* with length >= *min_len*."""
        node = self._root
        best_key: Optional[str] = None
        best_val: Optional[str] = None
        best_len = 0
        last_index = len(sanitized) - 1
        for i, ch in enumerate(sanitized):
            child = node.children.get(ch)
            if child is None:
                break
            node = child
            if node.key_at_end is not None and len(node.key_at_end) >= min_len:
                # Reject a match that splits a word in the query, so cached
                # "Moonstone" does not prefix-match "Moonstones".
                splits_word = (
                    i < last_index and _is_word_char(ch) and _is_word_char(sanitized[i + 1])
                )
                if not splits_word and len(node.key_at_end) > best_len:
                    best_key = node.key_at_end
                    best_val = node.value_at_end
                    best_len = len(node.key_at_end)
        if best_key is None or best_val is None:
            return None
        return best_key, best_val
