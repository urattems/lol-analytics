"""Standard-library-only bootstrap checks; never load .env or import the app."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys


def dependencies_ready(root: Path) -> bool:
    for raw in (root / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, expected = line.partition("==")
        if not separator:
            return False
        try:
            if version(name) != expected:
                return False
        except PackageNotFoundError:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(0 if dependencies_ready(Path(__file__).resolve().parent.parent) else 1)
