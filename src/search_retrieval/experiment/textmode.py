"""Text must decode as UTF-8 on every platform.

Libraries that open text files without naming an encoding (ir_datasets among them) follow the
interpreter default. On Windows that is a legacy code page: non-ASCII text is then read as
different characters without any error, and hashes and queries change with the platform.
Python's UTF-8 mode (`-X utf8` or PYTHONUTF8=1) makes the default UTF-8 everywhere.
"""

import subprocess
import sys


def utf8_mode() -> bool:
    return bool(sys.flags.utf8_mode)


def require_utf8_mode(reader: str) -> None:
    """Fail closed before a library that relies on the default encoding reads text."""
    if not utf8_mode():
        raise RuntimeError(f"{reader} needs Python UTF-8 mode: run with -X utf8 or PYTHONUTF8=1")


def rerun_in_utf8_mode() -> int | None:
    """Run this program again in UTF-8 mode when it was started without it.

    Returns the exit code of the second run, or None when the current process is already
    in UTF-8 mode and should continue.
    """
    if utf8_mode():
        return None
    return subprocess.call([sys.executable, "-X", "utf8", *sys.argv])
