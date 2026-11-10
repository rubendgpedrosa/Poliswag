"""Tests for modules.campfire (the API is faked; nothing leaves the test)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules import campfire


def _client(text, stop_reason="end_turn"):
    client = MagicMock()
    client.beta.messages.create = AsyncMock(
        return_value=SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
        )
    )
    return client


async def test_reads_names_from_every_image_in_one_call():
    client = _client(json.dumps({"names": ["4EAreia", " Ana ", "", "Scooby Jew"]}))
    names = await campfire.read_names(
        [(b"png-bytes", "image/png"), (b"jpg", "image/jpeg")], client=client
    )
    assert names == ["4EAreia", "Ana", "Scooby Jew"]
    request = client.beta.messages.create.call_args.kwargs
    images = [c for c in request["messages"][0]["content"] if c["type"] == "image"]
    assert [i["source"]["media_type"] for i in images] == ["image/png", "image/jpeg"]
    assert request["output_config"]["format"]["schema"] == campfire.SCHEMA
    assert request["model"] == "claude-sonnet-5-5"
    assert request["fallbacks"] == "default"


async def test_no_images_makes_no_call():
    client = _client("{}")
    assert await campfire.read_names([], client=client) == []
    client.beta.messages.create.assert_not_awaited()


@pytest.mark.parametrize(
    "text, stop_reason",
    [("{}", "end_turn"), ("not json", "end_turn"), ("", "refusal"), ("", "max_tokens")],
)
async def test_bad_answers_raise_a_campfire_error(text, stop_reason):
    with pytest.raises(campfire.CampfireError):
        await campfire.read_names(
            [(b"x", "image/png")], client=_client(text, stop_reason)
        )


async def test_missing_key_is_reported():
    with pytest.raises(campfire.CampfireError, match="ANTHROPIC_API_KEY"):
        await campfire.read_names([(b"x", "image/png")])
