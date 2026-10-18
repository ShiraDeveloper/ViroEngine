from __future__ import annotations

from pathlib import Path

PLACEHOLDER_PREFIXES = ("your_", "change", "example", "<")
SECRET_KEY_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CODE")
KNOWN_SECRET_PREFIXES = ("AIza", "sk-")


def find_violations(path: Path) -> list[str]:
    """Return sensitive variable names whose values look like real secrets."""
    violations: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue

        is_sensitive_name = any(marker in name.upper() for marker in SECRET_KEY_MARKERS)
        is_placeholder = not value or value.startswith(PLACEHOLDER_PREFIXES)
        is_non_placeholder_secret = is_sensitive_name and not is_placeholder
        has_known_secret_prefix = value.startswith(KNOWN_SECRET_PREFIXES)
        if is_non_placeholder_secret or has_known_secret_prefix:
            violations.append(name)
    return violations


def main() -> int:
    """Check the repository template and return 1 when a value looks secret."""
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    try:
        violations = find_violations(env_example)
    except OSError:
        print(".env.example")
        return 1

    for name in violations:
        print(name)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
