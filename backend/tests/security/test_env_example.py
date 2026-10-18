from __future__ import annotations

from scripts.check_env_example import main


def test_env_example_has_no_real_values() -> None:
    """The committed environment template contains no real secret values."""
    assert main() == 0
