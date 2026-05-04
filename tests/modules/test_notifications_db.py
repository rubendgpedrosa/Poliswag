"""Smoke-test the poracle DB schema expected by Notifications.

These run against the conftest autouse mock, so they verify SQL shape
(column names, table names) without a live database. A separate
integration-style comment documents what the init.sql must provide.
"""

from unittest.mock import MagicMock, patch

import pytest

from cogs.notifications import Notifications


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.poracle = MagicMock()
    poliswag.quest_search.pokemon_name_map = {"25": "pikachu"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(
        return_value=["25"]
    )
    with patch("cogs.notifications.DatabaseConnector"):
        c = Notifications(poliswag)
    c.poracle_db = MagicMock()
    return c


def test_resolve_targets_issues_channel_type_query(cog):
    cog.poracle_db.get_data_from_database.return_value = []
    cog._resolve_targets("raros")
    call_args = cog.poracle_db.get_data_from_database.call_args_list
    sql = " ".join(c.args[0] for c in call_args)
    assert "humans" in sql
    assert "discord:channel" in sql


def test_rule_exists_queries_monsters_table(cog):
    cog.poracle_db.get_data_from_database.return_value = []
    result = cog._rule_exists("111", 25, 0, 0)
    assert result is False
    sql = cog.poracle_db.get_data_from_database.call_args.args[0]
    assert "monsters" in sql
    assert "pokemon_id" in sql
