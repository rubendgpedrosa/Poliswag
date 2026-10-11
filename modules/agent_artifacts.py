"""Stage explicitly requested agent outputs where the bot can read them."""

import os
import stat
from pathlib import Path

MAX_ATTACHMENTS = 9  # Leave one Discord attachment slot for the text report.
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".csv",
    ".txt",
    ".json",
    ".svg",
}


def read_artifact(root, relative):
    root = Path(root).resolve()
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part.startswith(".") for part in path.parts)
    ):
        raise ValueError("Invalid attachment path")
    target = (root / path).resolve(strict=True)
    if not target.is_relative_to(root) or target.suffix.lower() not in EXTENSIONS:
        raise ValueError("Invalid attachment path or type")
    # Do not block on a FIFO or follow a final symlink swapped after resolution.
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise ValueError("Attachment is empty, too large or not a regular file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if not 0 < len(data) <= MAX_FILE_BYTES:
        raise ValueError("Attachment is empty or too large")
    return data


def stage_artifacts(worktree, directory, paths, revision):
    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError("Too many attachments")
    artifacts = []
    total = 0
    for index, relative in enumerate(paths):
        data = read_artifact(worktree, relative)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Attachments are too large")
        filename = Path(relative).name
        staged = Path("artifacts") / str(revision) / f"{index}-{filename}"
        target = Path(directory) / staged
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(data)
        target.chmod(0o600)
        artifacts.append({"path": staged.as_posix(), "filename": filename})
    return artifacts
