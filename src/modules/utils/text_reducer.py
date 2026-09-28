from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+")


def collapse_first_repeated_sequence(s: str) -> str:
    """
    Remove duplicate sequences at the beginning of a string.
    Example:
        This is a duplicate. This is a duplicate. This is a duplicate. This is a duplicate.
        ->
        This is a duplicate.
    """
    # Tokenize words and keep spans into original string
    words: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in _WORD_RE.finditer(s):
        words.append(m.group(0))
        spans.append((m.start(), m.end()))
    n = len(words)
    if n < 2:
        return s

    # Find first immediately repeated block starting at i of size k
    for i in range(n - 1):
        max_k = (n - i) // 2
        for k in range(1, max_k + 1):
            block = words[i:i + k]
            # Must repeat immediately
            if words[i + k:i + 2 * k] != block:
                continue
            # And from i to the end, it must be *only* repetitions of block
            tail = words[i:]
            if len(tail) % k != 0:
                continue
            reps = len(tail) // k
            if all(tail[j * k:(j + 1) * k] == block for j in range(reps)):
                # OK to collapse: keep prefix + one copy of the block (+ its trailing punctuation)
                end = spans[i + k - 1][1]
                j = end
                # include trailing punctuation directly after the block (stop at whitespace or word char/_)
                while j < len(s) and not s[j].isspace() and not s[j].isalnum() and s[j] != '_':
                    j += 1
                return s[:j]
            # Otherwise, unrepeated words exist at the end → do not dedupe
            return s
    return s
