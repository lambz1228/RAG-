"""Helpers for stable benchmark SVG snapshot comparison."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def normalize_svg(svg: str) -> str:
    text = str(svg or "").replace("\r\n", "\n").strip()
    text = re.sub(r">\s+<", "><", text)
    text = re.sub(r"\s+", " ", text)
    return text


def svg_snapshot_digest(svg: str) -> str:
    return hashlib.sha256(normalize_svg(svg).encode("utf-8")).hexdigest()


def read_snapshot(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_snapshot(path: str | Path, svg: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(svg).replace("\r\n", "\n"), encoding="utf-8")


def compare_snapshot(actual_svg: str, expected_path: str | Path) -> tuple[bool, str]:
    expected_path = Path(expected_path)
    if not expected_path.is_file():
        return False, f"missing snapshot: {expected_path}"
    expected = read_snapshot(expected_path)
    actual_digest = svg_snapshot_digest(actual_svg)
    expected_digest = svg_snapshot_digest(expected)
    if actual_digest == expected_digest:
        return True, actual_digest
    return False, f"digest mismatch: actual={actual_digest[:12]} expected={expected_digest[:12]}"
