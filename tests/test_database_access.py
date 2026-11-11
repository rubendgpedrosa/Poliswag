"""One way to the database.

The bot builds one handle per schema in main.py. A module asks the bot for
the handle it needs: a single statement goes through the handle's shared
connection, anything bigger through `handle.unit(work)`. These tests fail
when a module opens a connection for itself again, or builds a handle of its
own, which is how the bot came to have three ways of reaching the database.

Host scripts under scripts/ run outside the bot and are not checked.
"""

import ast
import inspect
from pathlib import Path

REPO = Path(__file__).parent.parent
BOT_CODE = [
    REPO / "main.py",
    *sorted((REPO / "modules").glob("*.py")),
    *sorted((REPO / "cogs").glob("*.py")),
]
CONNECTOR = "database_connector.py"


def _trees(skip):
    for path in BOT_CODE:
        if path.name in skip:
            continue
        yield path.relative_to(REPO), ast.parse(path.read_text())


def test_there_is_bot_code_to_check():
    assert len(BOT_CODE) > 80


def test_only_the_connector_opens_connections():
    offenders = []
    for path, tree in _trees({CONNECTOR}):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "modules.database_connector"
                and any(alias.name == "connect" for alias in node.names)
            ):
                offenders.append(f"{path}:{node.lineno} imports connect")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"pymysql", "database_connector"}
            ):
                offenders.append(f"{path}:{node.lineno} opens a connection")
    assert not offenders, (
        "Ask the bot's handle for a unit of work instead "
        "(await poliswag.<handle>.unit(work)): " + "; ".join(offenders)
    )


def test_handles_are_built_only_by_the_bot():
    offenders = []
    for path, tree in _trees({CONNECTOR, "main.py"}):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "DatabaseConnector"
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "Use a handle the bot already has (poliswag.db, .scanner_db, "
        ".site_db, .poracle_db): " + "; ".join(offenders)
    )


def test_the_bot_builds_one_handle_per_schema():
    import main

    tree = ast.parse(inspect.getsource(main))
    built = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "DatabaseConnector"
        ):
            target = node.targets[0]
            built[target.attr] = ast.unparse(node.value)
    assert set(built) == {"db", "scanner_db", "site_db", "poracle_db"}
    # Four handles, four different schemas.
    assert len(set(built.values())) == 4
