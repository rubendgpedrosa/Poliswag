import pytest

from modules.agent_artifacts import MAX_FILE_BYTES, read_artifact, stage_artifacts


def test_staged_image_survives_worktree_removal(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    image = worktree / "image.png"
    image.write_bytes(b"image")
    directory = tmp_path / "queue"
    artifacts = stage_artifacts(worktree, directory, ["image.png"], 2)
    image.unlink()
    worktree.rmdir()
    assert read_artifact(directory, artifacts[0]["path"]) == b"image"


@pytest.mark.parametrize(
    "path", ["../outside.png", "/tmp/outside.png", ".env", ".git/config", "missing.png"]
)
def test_invalid_paths_are_rejected(tmp_path, path):
    with pytest.raises((ValueError, OSError)):
        read_artifact(tmp_path, path)


def test_symlink_outside_worktree_is_rejected(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    (worktree / "image.png").symlink_to(outside)
    with pytest.raises(ValueError):
        read_artifact(worktree, "image.png")


def test_oversized_image_is_rejected(tmp_path):
    image = tmp_path / "image.png"
    with image.open("wb") as stream:
        stream.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(ValueError):
        read_artifact(tmp_path, "image.png")
