"""The guard that keeps game-side code out of this repository."""

import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "check_private_tech.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(GUARD), *args], capture_output=True, text=True, check=False
    )


def test_a_game_field_is_refused(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("FIELD = '" + "invited_" + "player_ids" + "'\n", encoding="utf-8")
    result = run(str(sample))
    assert result.returncode == 1
    assert "a field a game RPC returns" in result.stdout
    assert str(sample) in result.stdout


def test_a_hook_path_is_refused_even_when_it_does_not_exist():
    result = run("scripts/shiny_linker/phone.py")
    assert result.returncode == 1
    assert "host-side game session code" in result.stdout


def test_an_ordinary_file_passes(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "URL = 'https://polisnatch.pogoleiria.pt/v1/jobs'\n", encoding="utf-8"
    )
    result = run(str(sample))
    assert result.returncode == 0
    assert result.stdout == ""


def test_the_allow_mark_is_per_line(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "FIELD = '"
        + "invited_"
        + "player_ids'  # poliswag-guard: allow, documented fixture\n"
        "OTHER = '" + "invited_" + "player_ids'\n",
        encoding="utf-8",
    )
    result = run(str(sample))
    assert result.returncode == 1
    assert result.stdout.count("a field a game RPC returns") == 1
    assert ":2:" in result.stdout


def test_no_paths_is_a_usage_error():
    result = run()
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_the_owners_real_name_is_refused(tmp_path):
    sample = tmp_path / "sample.md"
    sample.write_text("author: " + "Rub" "en" + "\n", encoding="utf-8")
    result = run(str(sample))
    assert result.returncode == 1
    assert "the owner's real name" in result.stdout


def test_the_name_rule_does_not_match_the_github_handle(tmp_path):
    sample = tmp_path / "sample.md"
    sample.write_text("https://github.com/rubendgpedrosa\n", encoding="utf-8")
    assert run(str(sample)).returncode == 0
