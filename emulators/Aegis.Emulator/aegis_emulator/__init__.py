"""Standalone synthetic equipment emulator. Runtime dependencies: Python stdlib."""

from .core import SnapshotGenerator, create_server

__all__ = ["SnapshotGenerator", "create_server"]
