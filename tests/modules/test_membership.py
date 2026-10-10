from unittest.mock import MagicMock

from modules.membership import is_member


def guild_with(*member_ids):
    guild = MagicMock()
    guild.members = [MagicMock(id=i) for i in member_ids]
    guild.get_member = lambda user_id: user_id if user_id in member_ids else None
    return guild


def test_a_cached_member_is_a_member():
    assert is_member(MagicMock(guilds=[guild_with(1, 2)]), 2) is True


def test_someone_not_in_any_guild_is_not():
    assert is_member(MagicMock(guilds=[guild_with(1, 2)]), 3) is False


def test_an_empty_member_cache_fails_open():
    assert is_member(MagicMock(guilds=[guild_with()]), 3) is True


def test_no_guilds_at_all_fails_open():
    assert is_member(MagicMock(guilds=[]), 3) is True
