import pytest

from modules.agent_destination import delivery_reference


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Generate a leaderboard", None),
        ("Sned via DM", "dm"),
        ("envia por mensagem privada", "dm"),
        ("Send to #moderação", "#moderação"),
        ("publica no canal testes", "testes"),
        ("Send to <#123>", "<#123>"),
        ("Send via DM. Send to #moderação", "#moderação"),
        ("Send to #moderação. Send via DM", "dm"),
        ("Explain how shiny DMs work", None),
        ("Don't send via DM", None),
        ("Não envia por DM", None),
        ("Send to #moderação. Do not send via DM", "#moderação"),
    ],
)
def test_delivery_instructions(prompt, expected):
    assert delivery_reference(prompt) == expected
