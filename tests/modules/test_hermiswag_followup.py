"""Tests for modules.hermiswag_followup."""

import json
from datetime import timedelta
from types import SimpleNamespace as N

from modules.hermiswag_followup import (
    ACTIVE_WINDOW,
    MAX_CHAIN,
    MIN_GAP,
    QUIET,
    ActiveChannels,
    followup_reply,
    new_since_poliswag,
    utcnow,
)

OWN = 99


def _msg(mid, author, seconds_ago, now, mentions=(), bot=False):
    return N(
        id=mid,
        author=N(id=author, bot=bot),
        created_at=now - timedelta(seconds=seconds_ago),
        webhook_id=None,
        mentions=list(mentions),
        content="x",
    )


def test_new_messages_after_poliswag_once_people_went_quiet():
    now = utcnow()
    messages = [
        _msg(1, 5, 300, now),
        _msg(2, OWN, 200, now, bot=True),
        _msg(3, 5, 120, now),
        _msg(4, 6, QUIET + 5, now),
    ]
    assert [m.id for m in new_since_poliswag(messages, OWN, set(), now)] == [3, 4]


def test_waits_while_people_are_still_typing():
    now = utcnow()
    messages = [_msg(2, OWN, 200, now, bot=True), _msg(3, 5, 10, now)]
    assert new_since_poliswag(messages, OWN, set(), now) == []


def test_nothing_after_poliswag_or_already_seen_or_tagged():
    now = utcnow()
    assert new_since_poliswag([_msg(2, OWN, 200, now, bot=True)], OWN, set(), now) == []
    messages = [_msg(2, OWN, 200, now, bot=True), _msg(3, 5, 120, now)]
    assert new_since_poliswag(messages, OWN, {3}, now) == []
    tagged = [
        _msg(2, OWN, 200, now, bot=True),
        _msg(3, 5, 120, now, mentions=[N(id=OWN)]),
    ]
    assert new_since_poliswag(tagged, OWN, set(), now) == []
    assert new_since_poliswag([_msg(3, 5, 120, now)], OWN, set(), now) == []


def test_active_channels_window_gap_and_chain():
    active = ActiveChannels()
    active.spoke(1, invited=True, now=0)
    assert active.due(now=10) == [1]
    active.followed_up(1, now=10)
    assert active.due(now=10 + MIN_GAP - 1) == []
    assert active.due(now=10 + MIN_GAP) == [1]
    for i in range(MAX_CHAIN - 1):
        active.followed_up(1, now=1000 + i * MIN_GAP)
    assert active.due(now=1000 + MAX_CHAIN * MIN_GAP) == []
    active.spoke(1, invited=True, now=5000)  # a tag reopens it
    assert active.due(now=5000 + MIN_GAP) == [1]
    assert active.due(now=5000 + ACTIVE_WINDOW + 1) == []


def test_followup_reply_needs_for_me():
    assert followup_reply(json.dumps({"reply": "Sim!", "for_me": True})) == "Sim!"
    assert followup_reply(json.dumps({"reply": "Sim!", "for_me": False})) is None
    assert followup_reply(json.dumps({"reply": "", "for_me": True})) is None
    assert followup_reply("Sim!") is None
