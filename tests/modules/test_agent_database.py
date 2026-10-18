import json
import time
from unittest.mock import MagicMock

import pymysql
import pytest

from modules import agent_database
from modules.agent_database import AgentDatabase, query
from modules.agent_jobs import AgentJobs, read_json, write_json
from scripts import agent_db_mcp


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "UPDATE users SET id=1",
        "SELECT * INTO OUTFILE '/tmp/data' FROM users",
        "SELECT LOAD_FILE('/etc/passwd')",
        "/* comment */ DROP TABLE users",
    ],
)
def test_writes_and_file_access_rejected_before_connect(monkeypatch, sql):
    connect = MagicMock()
    monkeypatch.setattr(agent_database, "connect", connect)
    with pytest.raises(ValueError):
        query({"sql": sql})
    connect.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM account",
        "SELECT username FROM `dragonite`.`account`",
        "SELECT COUNT(*) FROM dragonite . device",
        "SELECT * FROM stats_accounts",
        "SELECT Password FROM mysql.user",
        "SELECT username FROM pokemon",
        "SELECT friend_code FROM trade_player",
        "SELECT map_token_hash FROM shiny_link",
        "SELECT * FROM acc/**/ount",
        "SELECT 1 -- account",
        "SELECT 1 AS a UNION SELECT * FROM trade_player",
        "SELECT pokemon_id FROM pokemon EXCEPT SELECT 1",
        "WITH t(a, b) AS (SELECT * FROM trade_player) SELECT b FROM t",
        "SHOW FULL PROCESSLIST",
        "SHOW BINLOG EVENTS",
        "SHOW ENGINE INNODB STATUS",
        "SELECT info FROM information_schema.PROCESSLIST",
        "SELECT trx_query FROM `information_schema` . `innodb_trx`",
    ],
)
def test_accounts_and_credentials_rejected_before_connect(monkeypatch, sql):
    connect = MagicMock()
    monkeypatch.setattr(agent_database, "connect", connect)
    with pytest.raises(ValueError, match="not available"):
        query({"sql": sql})
    connect.assert_not_called()


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW TABLES",
        "SHOW FULL COLUMNS FROM pokemon",
        "SHOW CREATE TABLE quest",
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
    ],
)
def test_schema_discovery_still_allowed(monkeypatch, sql):
    connection = MagicMock()
    connection.cursor.return_value.description = [("name",)]
    connection.cursor.return_value.fetchone.side_effect = [None]
    monkeypatch.setattr(agent_database, "connect", MagicMock(return_value=connection))
    assert query({"sql": sql})["rows"] == []


def test_dragonite_is_not_an_alias():
    assert "dragonite" not in agent_database.databases()
    with pytest.raises(ValueError, match="Unknown database"):
        query({"database": "dragonite", "sql": "SELECT 1"})


def test_pokemon_name_dragonite_is_still_queryable(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.description = [("n",)]
    connection.cursor.return_value.fetchone.side_effect = [{"n": 3}, None]
    monkeypatch.setattr(agent_database, "connect", MagicMock(return_value=connection))
    sql = "SELECT COUNT(*) AS n FROM pokemon_name WHERE name = 'Dragonite'"
    assert query({"sql": sql})["rows"] == [{"n": 3}]


def test_select_star_redacts_sensitive_columns(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.description = [("pokemon_id",), ("username",), ("username",)]
    cursor.fetchone.side_effect = [
        {"pokemon_id": 149, "username": "scanner01", "t.username": "trainer"},
        None,
    ]
    monkeypatch.setattr(agent_database, "connect", MagicMock(return_value=connection))
    (row,) = query({"sql": "SELECT * FROM pokemon p JOIN trade_player t"})["rows"]
    assert row == {
        "pokemon_id": 149,
        "username": "[redacted]",
        "t.username": "[redacted]",
    }


def test_live_query_uses_read_only_transaction_and_bounds_stream(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.description = [("count",)]
    cursor.fetchone.side_effect = [{"count": 42}, {"count": 17}, {"count": 9}]
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(agent_database, "connect", connect)
    result = query(
        {
            "sql": "SELECT count FROM totals WHERE area=%s",
            "params": ["Leiria"],
            "max_rows": 2,
        }
    )
    assert result["rows"] == [{"count": 42}, {"count": 17}]
    assert result["truncated"]
    assert [call.args[0] for call in cursor.execute.call_args_list[:2]] == [
        "SET SESSION max_statement_time=10",
        "START TRANSACTION READ ONLY",
    ]
    assert cursor.execute.call_args.args[1] == ["Leiria"]
    connection.close.assert_called_once()
    cursor.close.assert_not_called()
    connection.commit.assert_not_called()


def test_failed_query_closes_connection(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.execute.side_effect = pymysql.OperationalError(
        2013, "lost"
    )
    monkeypatch.setattr(agent_database, "connect", MagicMock(return_value=connection))
    with pytest.raises(pymysql.MySQLError):
        query({"sql": "SELECT 1"})
    connection.close.assert_called_once()


def request(jobs, owner=123, state="running", expired=False):
    directory = jobs.directory("abcdef123456")
    directory.mkdir(parents=True)
    write_json(
        directory / "request.json",
        {"id": directory.name, "owner_id": str(owner), "created": time.time()},
    )
    write_json(directory / "result.json", {"state": state})
    (directory / "database").mkdir()
    path = directory / "database" / "request.request.json"
    write_json(
        path,
        {
            "tool": "query",
            "arguments": {"sql": "SELECT 1"},
            "created": time.time() - (40 if expired else 0),
        },
    )
    return directory, path.with_name("request.result.json")


@pytest.mark.parametrize(
    "owner,state",
    [
        (999, "running"),
        (0, "running"),
        (123, "completed"),
        (123, "queued"),
        (123, "waiting_input"),
    ],
)
async def test_only_running_owner_jobs_can_query(tmp_path, monkeypatch, owner, state):
    jobs = AgentJobs(tmp_path)
    _, response = request(jobs, owner, state)
    execute = MagicMock()
    monkeypatch.setattr(agent_database, "query", execute)
    await AgentDatabase(jobs, 123).poll()
    execute.assert_not_called()
    assert not response.exists()


async def test_active_owner_queries_are_served_once(tmp_path, monkeypatch):
    jobs = AgentJobs(tmp_path)
    _, response = request(jobs)
    execute = MagicMock(return_value={"rows": [{"value": 1}]})
    monkeypatch.setattr(agent_database, "query", execute)
    bridge = AgentDatabase(jobs, 123)
    await bridge.poll()
    await bridge.poll()
    execute.assert_called_once()
    assert read_json(response)["data"]["rows"] == [{"value": 1}]


async def test_owner_unfiltered_report_uses_history_tool(tmp_path, monkeypatch):
    jobs = AgentJobs(tmp_path)
    directory, response = request(jobs)
    path = directory / "database" / "request.request.json"
    data = read_json(path)
    write_json(
        path, {**data, "tool": "spawn_history_report", "arguments": {"hours": 24}}
    )
    report = {
        "metadata": {"complete_window": False},
        "trainers": [{"trainer": "Trainer", "predicted_shinies": 2}],
    }
    execute = MagicMock(return_value=report)
    monkeypatch.setattr(agent_database, "leaderboard", execute)
    await AgentDatabase(jobs, 123).poll()
    execute.assert_called_once_with(24)
    assert read_json(response)["data"] == report


@pytest.mark.parametrize("cancelled,expired", [(True, False), (False, True)])
async def test_cancelled_or_expired_queries_do_not_execute(
    tmp_path, monkeypatch, cancelled, expired
):
    jobs = AgentJobs(tmp_path)
    directory, _ = request(jobs, expired=expired)
    if cancelled:
        (directory / "cancel").touch()
    execute = MagicMock()
    monkeypatch.setattr(agent_database, "query", execute)
    await AgentDatabase(jobs, 123).poll()
    execute.assert_not_called()


async def test_connection_errors_do_not_leak_credentials(tmp_path, monkeypatch):
    jobs = AgentJobs(tmp_path)
    _, response = request(jobs)
    monkeypatch.setattr(
        agent_database,
        "query",
        MagicMock(side_effect=pymysql.OperationalError(1045, "secret user password")),
    )
    await AgentDatabase(jobs, 123).poll()
    assert "secret" not in response.read_text()
    assert read_json(response)["code"] == 1045


def test_mcp_initializes_and_exposes_database_tools(tmp_path):
    response = agent_db_mcp.handle(
        {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        tmp_path,
    )
    assert response["result"]["protocolVersion"] == "2025-06-18"
    tools = agent_db_mcp.handle({"id": 2, "method": "tools/list"}, tmp_path)["result"][
        "tools"
    ]
    assert {tool["name"] for tool in tools} == {
        "list_databases",
        "query",
        "spawn_history_report",
    }
    assert (
        agent_db_mcp.handle({"method": "notifications/initialized"}, tmp_path) is None
    )


def test_mcp_reports_query_results_and_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_db_mcp, "call", lambda *args: {"data": {"rows": [{"value": 1}]}}
    )
    response = agent_db_mcp.handle(
        {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query", "arguments": {"sql": "SELECT 1"}},
        },
        tmp_path,
    )["result"]
    assert not response["isError"]
    assert json.loads(response["content"][0]["text"])["data"]["rows"] == [{"value": 1}]
    response = agent_db_mcp.handle(
        {"id": 2, "method": "tools/call", "params": {"name": "delete"}}, tmp_path
    )["result"]
    assert response["isError"]


def test_mcp_stops_waiting_when_job_cancelled(tmp_path):
    write_json(tmp_path / "result.json", {"state": "running"})
    (tmp_path / "cancel").touch()
    assert "error" in agent_db_mcp.call(tmp_path, "query", {"sql": "SELECT 1"})
    assert list((tmp_path / "database").iterdir()) == []
