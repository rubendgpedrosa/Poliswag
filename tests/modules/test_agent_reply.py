import pytest

from modules.agent_reply import parse_reply


def reply(**changes):
    return {
        "outcome": "done",
        "kind": "chat",
        "reply": "Olá! 🐸",
        "details": "",
        **changes,
    }


def test_multiple_sentences_do_not_become_a_long_chat_message():
    result = parse_reply(
        reply(reply="Já ficou corrigido. Agora vou explicar tudo.\nLong report.")
    )
    assert result["reply"] == "Já ficou corrigido."


def test_long_sentences_are_bounded_without_removing_report_details():
    details = "Leiria: 42 Pokémon\nMarinha: 17 raids"
    result = parse_reply(reply(reply="palavra " * 100, kind="stats", details=details))
    assert len(result["reply"]) <= 280
    assert result["details"] == details


def test_attachment_manifest_is_preserved_and_old_replies_remain_readable():
    assert parse_reply(reply(attachments=["reports/image.png"]))["attachments"] == [
        "reports/image.png"
    ]
    assert parse_reply(reply())["attachments"] == []


@pytest.mark.parametrize("attachments", ["image.png", [123], [""], ["image.png"] * 10])
def test_malformed_attachment_manifest_is_rejected(attachments):
    with pytest.raises(ValueError):
        parse_reply(reply(attachments=attachments))


@pytest.mark.parametrize(
    "value",
    [
        "Still working; shall I continue?",
        {},
        {"reply": "done"},
        reply(outcome="running"),
        reply(kind="progress"),
        reply(reply=""),
        reply(details=[]),
    ],
)
def test_unfinished_progress_or_malformed_output_is_not_completion(value):
    with pytest.raises(ValueError):
        parse_reply(value)
