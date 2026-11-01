"""Unit tests for in-memory tar archive generation and remote streaming."""

import io
import tarfile
from pathlib import Path

from aayi.docker.tar_builder import create_in_memory_archive


def test_create_in_memory_archive_filtering(tmp_path: Path):
    # Setup test project files
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM alpine", encoding="utf-8")
    (tmp_path / "secret.env").write_text("SECRET=123", encoding="utf-8")

    # Setup directories that should be excluded
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config", encoding="utf-8")

    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "lib.py").write_text("venv code", encoding="utf-8")

    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "compiled.pyc").write_text("binary", encoding="utf-8")

    # Add custom .dockerignore
    (tmp_path / ".dockerignore").write_text("secret.env\n", encoding="utf-8")

    archive_bytes = create_in_memory_archive(tmp_path)
    assert len(archive_bytes) > 0

    # Read archive back in memory and inspect members
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        names = tar.getnames()

        # Included files
        assert "main.py" in names
        assert "Dockerfile" in names

        # Excluded files
        assert ".git" not in names
        assert ".git/config" not in names
        assert ".venv" not in names
        assert ".venv/lib.py" not in names
        assert "__pycache__" not in names
        assert "secret.env" not in names
