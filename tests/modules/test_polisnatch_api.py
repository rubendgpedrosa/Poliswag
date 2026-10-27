"""Tests for modules.polisnatch_api: the contract the bot and linker share."""

import pytest

from modules.polisnatch_api import (
    DEFAULT_JOBS_URL,
    JobApiError,
    auth_headers,
    check,
    decode,
    error_for,
    health_url,
    using_default_url,
    job_path,
    jobs_url,
    missing_key_error,
    read_key,
)


class TestUrls:
    def test_default_jobs_url(self):
        assert jobs_url({}) == DEFAULT_JOBS_URL

    def test_env_override_and_trailing_slash(self):
        env = {"POLISNATCH_JOBS_URL": "https://jobs.test/v1/jobs/"}
        assert jobs_url(env) == "https://jobs.test/v1/jobs"

    def test_health_is_outside_the_jobs_base(self):
        assert health_url("https://jobs.test/v1/jobs") == "https://jobs.test/v1/health"

    def test_health_keeps_an_unusual_base(self):
        assert health_url("http://127.0.0.1:9300") == "http://127.0.0.1:9300/v1/health"


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

    def test_missing_key_error_names_the_env_var_and_file(self, tmp_path):
        env = {"POLISNATCH_JOBS_KEY_FILE": str(tmp_path / "nothing")}
        error = missing_key_error(env)
        assert isinstance(error, JobApiError)
        assert error.code == "no_key"
        assert error.status is None
        assert "POLISNATCH_JOBS_KEY" in str(error)
        assert str(tmp_path / "nothing") in str(error)

    def test_bearer_header_never_an_empty_header(self):
        assert auth_headers("abc") == {"Authorization": "Bearer abc"}
        assert auth_headers("") == {}


class TestJobPath:
    def test_kind_and_id(self):
        assert job_path("lure", "job_0123456789abcdef") == "/lure/job_0123456789abcdef"

    def test_unknown_kind(self):
        with pytest.raises(JobApiError) as exc:
            job_path("phone", "job_1")
        assert exc.value.code == "bad_kind"

    def test_missing_id(self):
        with pytest.raises(JobApiError) as exc:
            job_path("link", "")
        assert exc.value.code == "bad_job_id"

    def test_id_is_quoted(self):
        assert job_path("link", "a/b") == "/link/a%2Fb"


class TestPayloads:
    def test_decode_json_object(self):
        assert decode(b'{"jobId": "job_1"}') == {"jobId": "job_1"}

    def test_decode_rejects_empty_and_non_objects(self):
        assert decode(b"") == {}
        assert decode(b"not json") == {}
        assert decode(b"[1, 2]") == {}

    def test_check_returns_the_expected_body(self):
        assert check(202, {"jobId": "job_1"}, (202,)) == {"jobId": "job_1"}
        assert check(200, {}, (200, 202)) == {}

    def test_check_raises_with_the_server_error(self):
        body = {"error": "jobs_disabled", "message": "jobs are not enabled"}
        with pytest.raises(JobApiError) as exc:
            check(503, body, (202,))
        assert exc.value.status == 503
        assert exc.value.code == "jobs_disabled"
        assert "not enabled" in str(exc.value)

    def test_error_for_bounds_the_message(self):
        error = error_for(400, {"error": "bad", "message": "x" * 500})
        assert error.status == 400
        assert len(str(error)) == 200

    def test_error_for_falls_back_to_the_code(self):
        error = error_for(401, {"error": "unauthorized"})
        assert str(error) == "unauthorized"


def test_using_default_url_reports_a_missing_setting():
    assert using_default_url({}) is True
    assert using_default_url({"POLISNATCH_JOBS_URL": "   "}) is True
    assert using_default_url({"POLISNATCH_JOBS_URL": "https://host/v1/jobs"}) is False
