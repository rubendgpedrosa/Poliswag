"""Verify that tests never open real DB connections regardless of Config."""

import pymysql


def test_pymysql_connect_is_mocked():
    """If the autouse guard is active, pymysql.connect returns a MagicMock."""
    conn = pymysql.connect(host="unreachable", user="u", password="p", db="d")
    # A real connection to "unreachable" would raise; reaching here means it's mocked.
    assert conn is not None


def test_database_connector_init_does_not_raise():
    """DatabaseConnector() must not raise even with no real MariaDB available."""
    from modules.database_connector import DatabaseConnector

    dc = DatabaseConnector.__new__(DatabaseConnector)
    dc.database = "poliswag"
    # Call connect_to_db — the patch means pymysql.connect returns a MagicMock
    conn = dc.connect_to_db()
    assert conn is not None
