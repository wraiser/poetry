import sys

from contextlib import suppress
from typing import List
from typing import Optional


try:
    from importlib import metadata
except ImportError:
    # compatibility for python <3.8
    import importlib_metadata as metadata  # noqa: F401, TC002

WINDOWS = sys.platform == "win32"


def decode(string: str, encodings: Optional[List[str]] = None) -> str:
    if not isinstance(string, bytes):
        return string

    encodings = encodings or ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        with suppress(UnicodeEncodeError, UnicodeDecodeError):
            return string.decode(encoding)

    return string.decode(encodings[0], errors="ignore")


def encode(string: str, encodings: Optional[List[str]] = None) -> bytes:
    if isinstance(string, bytes):
        return string

    encodings = encodings or ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        with suppress(UnicodeEncodeError, UnicodeDecodeError):
            return string.encode(encoding)

    return string.encode(encodings[0], errors="ignore")


def to_str(string: str) -> str:
    return decode(string)


def list_to_shell_command(cmd: List[str]) -> str:
    return " ".join(
        f'"{token}"' if " " in token and token[0] not in {"'", '"'} else token
        for token in cmd
    )
