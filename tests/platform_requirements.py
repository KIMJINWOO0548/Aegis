"""Test prerequisites for intentionally platform-limited production paths.

Only tests whose assertions require reaching that path import the markers.
A separate Windows suite asserts unsupported operations do not touch data.
"""
import os

import pytest


def requires_posix(*primitives):
    unmet = os.name != "posix" or not all(hasattr(os, name) for name in primitives)
    return pytest.mark.skipif(
        unmet, reason=f"POSIX storage path requires {', '.join(primitives)}"
    )


requires_posix_archive_publication = requires_posix("O_DIRECTORY")
requires_posix_nullable_archive = requires_posix("O_NOFOLLOW", "O_DIRECTORY")
requires_posix_storage_diagnostics = requires_posix("O_NOFOLLOW", "statvfs")
fifo_case = pytest.param(
    "fifo",
    marks=pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture requires os.mkfifo"),
)
