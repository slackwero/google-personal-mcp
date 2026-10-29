"""Safe saving of attachments. The filename comes from the sender, so it is treated as hostile."""

import os
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path

MAX_FILENAME_LENGTH = 150
# APFS allows 255 UTF-16 units; 240 UTF-8 bytes leaves room for the "-1000" suffix and is safe on exFAT/SMB.
MAX_FILENAME_BYTES = 240
MAX_COLLISION_TRIES = 1000
DIR_MODE = 0o700
FILE_MODE = 0o600
# O_NOFOLLOW is defense in depth: with O_EXCL an existing symlink already fails with FileExistsError.
OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def _clean(raw: str) -> str:
    """Keep only the last path component (either separator style), drop control characters and leading dots."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp"})
    return re.sub(r"^[\s.]+", "", name).rstrip()


def _fits(name: str) -> bool:
    return len(name) <= MAX_FILENAME_LENGTH and len(name.encode()) <= MAX_FILENAME_BYTES


def _cap(name: str) -> str:
    """Trim the stem (never the extension) until the name fits both the character and the byte cap."""
    if _fits(name):
        return name
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or not _fits(extension):
        stem, extension = name, ""
    suffix = f".{extension}" if extension else ""
    # Slicing a str cuts on code points, never inside one.
    while stem and not _fits(stem + suffix):
        stem = stem[:-1]
    return (stem + suffix) or "attachment"


def sanitize_filename(raw: str, *, fallback: str) -> str:
    """Return a safe single-component filename; `fallback` (cleaned the same way) when nothing usable is left."""
    name = _clean(raw) or _clean(fallback) or "attachment"
    return _cap(name)


def ensure_inside(path: Path, download_dir: Path) -> None:
    """Raise ValueError unless `path` resolves to a file directly inside `download_dir`."""
    if path.resolve().parent != download_dir.resolve():
        raise ValueError("Refusing to save outside the download folder")


def _candidates(name: str) -> Iterator[str]:
    yield name
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        stem, extension = name, ""
    suffix = f".{extension}" if extension else ""
    for number in range(1, MAX_COLLISION_TRIES + 1):
        yield f"{stem}-{number}{suffix}"


def save_attachment(data: bytes, raw_filename: str, download_dir: Path, *, fallback: str) -> Path:
    """Write `data` to a new private file in `download_dir` and return its path. Never overwrites or follows links.

    A missing download folder is created with mode 0700; an existing one keeps its mode. The returned path is absolute.

    Raises ValueError when the name cannot be placed safely and OSError when the write fails.
    """
    name = sanitize_filename(raw_filename, fallback=fallback)
    download_dir = download_dir.absolute()
    download_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    for candidate in _candidates(name):
        path = download_dir / candidate
        try:
            descriptor = os.open(path, OPEN_FLAGS, FILE_MODE)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            ensure_inside(path, download_dir)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    raise ValueError(f"Could not find a free name for {name!r} after {MAX_COLLISION_TRIES} tries")
