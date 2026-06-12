"""
Unit tests for features/booleans.py.

No database required.

Run with:
    source .venv/bin/activate
    PYTHONPATH=. python3 tests/test_booleans.py
"""

from __future__ import annotations

from features.booleans import encode_tristate

_CASES: list[tuple[bool | None, int]] = [
    (True,  1),   # confirmed yes
    (False, 0),   # confirmed no
    (None, -1),   # unknown — meaningfully different from no
]


def test_encode_tristate() -> None:
    for val, expected in _CASES:
        result = encode_tristate(val)
        assert result == expected, (
            f"FAIL encode_tristate({val!r}): expected {expected!r}, got {result!r}"
        )
        print(f"OK  encode_tristate({str(val)!r:6}) → {result!r}")


if __name__ == "__main__":
    test_encode_tristate()
    print("\nAll boolean encoding tests passed.")
