"""Tests for modules.polisnatch_api: the submit key for the status poll."""

from modules.polisnatch_api import DEFAULT_KEY_FILE, auth_headers, key_file, read_key


class TestKey:
    def test_env_key_wins_over_the_file(self, tmp_path):
        path = tmp_path / "jobs.key"
        path.write_text("from-file\n", encoding="utf-8")
        env = {
            "POLISNATCH_JOBS_KEY": " from-env ",
            "POLISNATCH_JOBS_KEY_FILE": str(path),
        }
        assert read_key(env) == "from-env"

    def test_file_key(self, tmp_path):
        path = tmp_path / "jobs.key"
        path.write_text(" from-file\n", encoding="utf-8")
        assert read_key({"POLISNATCH_JOBS_KEY_FILE": str(path)}) == "from-file"

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        env = {"POLISNATCH_JOBS_KEY_FILE": str(tmp_path / "nothing")}
        assert read_key(env) == ""

    def test_the_file_has_a_default_place(self):
        assert key_file({}) == DEFAULT_KEY_FILE
        assert key_file({"POLISNATCH_JOBS_KEY_FILE": "/elsewhere"}) == "/elsewhere"

    def test_bearer_header_never_an_empty_header(self):
        assert auth_headers("abc") == {"Authorization": "Bearer abc"}
        assert auth_headers("") == {}
