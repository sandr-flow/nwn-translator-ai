"""Translation cache with trie-backed longest-prefix lookup for journal chains."""

from __future__ import annotations

from typing import Dict, Iterator, Optional, Tuple


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
        for ch in sanitized:
            child = node.children.get(ch)
            if child is None:
                break
            node = child
            if node.key_at_end is not None and len(node.key_at_end) >= min_len:
                if len(node.key_at_end) > best_len:
                    best_key = node.key_at_end
                    best_val = node.value_at_end
                    best_len = len(node.key_at_end)
        if best_key is None or best_val is None:
            return None
        return best_key, best_val
