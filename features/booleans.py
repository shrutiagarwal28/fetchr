"""
Three-state boolean encoding for nullable compatibility and medical fields.

A dog that has never been tested with cats is meaningfully different from a dog
that is confirmed bad with cats. Encoding null as -1 (unknown) rather than 0
(false) preserves that distinction for the matching model.
"""

from __future__ import annotations

from typing import Optional


def encode_tristate(val: Optional[bool]) -> int:
    """
    Encode a nullable boolean as a three-state integer.

      True  →  1  (confirmed yes)
      False →  0  (confirmed no)
      None  → -1  (unknown / not tested — not the same as no)
    """
    if val is True:
        return 1
    if val is False:
        return 0
    return -1
