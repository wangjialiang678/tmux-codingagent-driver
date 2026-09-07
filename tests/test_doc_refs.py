"""Regression gate for repository-relative references in Markdown."""
from __future__ import annotations

import subprocess
from pathlib import Path


def test_document_references_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["python3", "tools/check_doc_refs.py", "--json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="")
    assert result.returncode == 0
