import time
from decimal import Decimal

import pytest

from modules.public_map_context import BBOX, PublicMapContext, _integer

NAMES = {"149": "Dragonite", "42": "Golbat", "1": "Bulbasaur"}


class FakeDB:
    def __init__(self, handler=None):
        self.queries = []
        self.handler = handler

    async def get_data_from_database(self, query, params=None):
        self.queries.append((query, params))
        if self.handler is not None:
            result = self.handler(query, params)
            if isinstance(result, Exception):
                raise result
            return result
        return []


def future(seconds=3600):
    return time.time() + seconds


def spawn_row(**changes):
    row = {
        "pokemon_id": 149,
        "lat": 39.74,
        "lon": -8.80,
        "expire_timestamp": future(),
    }
    row.update(changes)
    return row


async def test_greeting_never_touches_the_database():
    def handler(query, params):
        raise AssertionError("a greeting must not query the map")

    db = FakeDB(handler)
    context = PublicMapContext(db, NAMES)
    assert await context.snapshot("Olá, tudo bem?") is None
    assert db.queries == []


async def test_recognized_species_uses_parameterized_ids_and_fixed_bounds():
    rows = [
        spawn_row(),
        spawn_row(lat=40.0),
        spawn_row(expire_timestamp=future(-60)),
        spawn_row(pokemon_id=999),
    ]
    db = FakeDB(lambda query, params: rows)
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("Onde há Dragonite?")

    query, params = db.queries[0]
    assert query.startswith(
        "SELECT pokemon_id, lat, lon, expire_timestamp FROM pokemon"
    )
    assert "SELECT *" not in query
    assert "seen_type = 'encounter'" in query
    assert "expire_timestamp_verified = 1" in query
    assert "expire_timestamp > UNIX_TIMESTAMP()" in query
    assert "lat BETWEEN %s AND %s" in query and "lon BETWEEN %s AND %s" in query
    assert "pokemon_id IN (%s)" in query and "LIMIT 21" in query
    assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3], 149)
    assert "Dragonite" not in query
    section = snapshot["sections"][0]
    assert section["recognized"] is True
    assert len(section["locations"]) == 1
    assert section["locations"][0]["species"] == "Dragonite"
    assert section["locations"][0]["lat"] == 39.74
    assert section["locations"][0]["expires_at"]


async def test_unrecognized_spawn_question_returns_bounded_species_groups():
    db = FakeDB(
        lambda query, params: [
            {"pokemon_id": 149, "count": 4},
            {"pokemon_id": 999, "count": 2},
            {"pokemon_id": 42, "count": 0},
        ]
    )
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("O que apareceu no mapa?")

    query, params = db.queries[0]
    assert "COUNT(*) AS count" in query and "GROUP BY pokemon_id" in query
    assert "SELECT *" not in query and "LIMIT 20" in query
    assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3])
    section = snapshot["sections"][0]
    assert section["recognized"] is False
    assert section["groups"] == [{"species": "Dragonite", "count": 4}]


async def test_raids_distinguish_eggs_from_active_bosses():
    now = time.time()
    db = FakeDB(
        lambda query, params: [
            {
                "name": "Ginásio Centro",
                "lat": 39.74,
                "lon": -8.80,
                "raid_level": 5,
                "raid_pokemon_id": 149,
                "raid_end_timestamp": now + 1800,
                "raid_battle_timestamp": now - 300,
            },
            {
                "name": "Ginásio Jardim",
                "lat": 39.75,
                "lon": -8.81,
                "raid_level": 3,
                "raid_pokemon_id": 0,
                "raid_end_timestamp": now + 900,
                "raid_battle_timestamp": now + 600,
            },
            {
                "name": "Fora",
                "lat": 12.0,
                "lon": 12.0,
                "raid_level": 5,
                "raid_pokemon_id": 149,
                "raid_end_timestamp": now + 600,
                "raid_battle_timestamp": now - 60,
            },
        ]
    )
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("Quais são as raids?")

    query, params = db.queries[0]
    assert "FROM gym" in query and "deleted = 0" in query
    assert "raid_level > 0" in query
    assert "raid_end_timestamp > UNIX_TIMESTAMP()" in query
    assert "SELECT *" not in query and "LIMIT 20" in query
    assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3])
    items = snapshot["sections"][0]["items"]
    assert [item["status"] for item in items] == ["active", "egg"]
    assert items[0]["species"] == "Dragonite" and items[0]["level"] == 5
    assert "hatches_at" not in items[0]
    assert items[1]["species"] is None
    assert "hatches_at" in items[1]
    assert items[1]["hatches_at"] != items[1]["ends_at"]


async def test_lures_are_future_only_and_bounded():
    now = time.time()
    db = FakeDB(
        lambda query, params: [
            {
                "name": "Praça",
                "lat": 39.74,
                "lon": -8.80,
                "lure_id": 501,
                "lure_expire_timestamp": now + 1200,
            },
            {
                "name": "Expirada",
                "lat": 39.75,
                "lon": -8.81,
                "lure_id": 501,
                "lure_expire_timestamp": now - 1,
            },
            {
                "name": "Fora",
                "lat": 1.0,
                "lon": 1.0,
                "lure_id": 501,
                "lure_expire_timestamp": now + 1200,
            },
        ]
    )
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("Onde há lures?")

    query, params = db.queries[0]
    assert "FROM pokestop" in query and "deleted = 0" in query
    assert "lure_expire_timestamp > UNIX_TIMESTAMP()" in query
    assert "SELECT *" not in query and "LIMIT 20" in query
    assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3])
    items = snapshot["sections"][0]["items"]
    assert [item["name"] for item in items] == ["Praça"]


async def test_quests_use_fixed_columns_and_alternative_counterparts():
    now = time.time()

    def handler(query, params):
        if "alternative_quest_title" in query:
            return [
                {
                    "name": "Parque",
                    "lat": 39.75,
                    "lon": -8.81,
                    "alternative_quest_title": "Catch 3 Dragonite",
                    "alternative_quest_reward_type": 7,
                    "alternative_quest_pokemon_id": 149,
                    "alternative_quest_reward_amount": 1,
                    "alternative_quest_expiry": now + 1800,
                }
            ]
        return [
            {
                "name": "Centro",
                "lat": 39.74,
                "lon": -8.80,
                "quest_title": "Catch 5 Pokémon",
                "quest_reward_type": 7,
                "quest_pokemon_id": 149,
                "quest_reward_amount": 1,
                "quest_expiry": now + 3600,
            }
        ]

    db = FakeDB(handler)
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("Quests de Dragonite?")

    assert len(db.queries) == 2
    for query, params in db.queries:
        assert "FROM pokestop" in query and "deleted = 0" in query
        assert "_reward_type IS NOT NULL" in query
        assert "_expiry > UNIX_TIMESTAMP()" in query
        assert "lat BETWEEN %s AND %s" in query and "lon BETWEEN %s AND %s" in query
        assert "_pokemon_id IN (%s)" in query
        assert "SELECT *" not in query and "LIMIT 20" in query
        assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3], 149)
        assert "quest_rewards" not in query
    items = snapshot["sections"][0]["items"]
    assert len(items) == 2
    assert {item["ar"] for item in items} == {False, True}
    assert all(item["species"] == "Dragonite" for item in items)
    assert all(item["reward_label"] == "Dragonite encounter" for item in items)
    assert all("reward_type" not in item for item in items)


@pytest.mark.parametrize(
    "question, reward_type",
    [
        ("Quests de Stardust?", 3),
        ("Onde há quests de encounter?", 7),
        ("Quests de Mega Energy?", 12),
        ("Quests de XL Candy?", 13),
        ("Quests de Candy?", 4),
        ("Quests de XP?", 1),
        ("Quests de items?", 2),
    ],
)
async def test_quest_reward_filters_are_fixed_and_parameterized(question, reward_type):
    db = FakeDB(lambda query, params: [])
    context = PublicMapContext(db, NAMES)

    await context.snapshot(question)

    assert len(db.queries) == 2
    for query, params in db.queries:
        assert "_reward_type IN (%s)" in query
        assert params == (BBOX[0], BBOX[2], BBOX[1], BBOX[3], reward_type)


async def test_unknown_quest_reward_label_preserves_uncertainty():
    db = FakeDB(
        lambda query, params: (
            [
                {
                    "name": "Praça",
                    "lat": 39.74,
                    "lon": -8.8,
                    "quest_title": "A mystery",
                    "quest_reward_type": 99,
                    "quest_pokemon_id": None,
                    "quest_reward_amount": None,
                    "quest_expiry": time.time() + 3600,
                }
            ]
            if "alternative_quest_title" not in query
            else []
        )
    )
    context = PublicMapContext(db, NAMES)
    snapshot = await context.snapshot("Quests?")
    item = snapshot["sections"][0]["items"][0]
    assert item["reward_label"] == "Reward"
    assert item["species"] is None


async def test_map_fallbacks_use_english_game_terms():
    now = time.time()
    db = FakeDB(
        lambda query, params: (
            [
                {
                    "name": None,
                    "lat": 39.74,
                    "lon": -8.8,
                    "raid_level": 5,
                    "raid_pokemon_id": 149,
                    "raid_end_timestamp": now + 1800,
                    "raid_battle_timestamp": now - 60,
                }
            ]
            if "FROM gym" in query
            else [
                {
                    "name": None,
                    "lat": 39.74,
                    "lon": -8.8,
                    "lure_id": 501,
                    "lure_expire_timestamp": now + 1200,
                }
            ]
        )
    )
    context = PublicMapContext(db, NAMES)
    raids = await context.snapshot("Raids?")
    lures = await context.snapshot("Lures?")
    assert raids["sections"][0]["items"][0]["name"] == "Gym"
    assert lures["sections"][0]["items"][0]["name"] == "Pokéstop"


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "1.5",
        1.5,
        Decimal("1.5"),
        Decimal("NaN"),
        None,
        True,
    ],
)
def test_integer_rejects_non_finite_or_fractional_values(value):
    assert _integer(value, 0, 10) is None


def test_integer_accepts_decimal_and_numeric_strings():
    assert _integer(Decimal("5"), 0, 10) == 5
    assert _integer("5", 0, 10) == 5


async def test_partial_failure_marks_only_that_section_unavailable():
    def handler(query, params):
        if "FROM gym" in query:
            raise RuntimeError("database unavailable")
        return [
            {
                "pokemon_id": 42,
                "count": 2,
            }
        ]

    db = FakeDB(handler)
    context = PublicMapContext(db, NAMES)

    snapshot = await context.snapshot("spawns e raids")

    sections = {section["kind"]: section for section in snapshot["sections"]}
    assert sections["spawns"]["status"] == "ok"
    assert sections["raids"] == {"kind": "raids", "status": "unavailable"}


async def test_user_sql_never_reaches_the_database():
    db = FakeDB(lambda query, params: [spawn_row()])
    context = PublicMapContext(db, NAMES)

    await context.snapshot("Dragonite' OR 1=1; SELECT * FROM account --")

    assert len(db.queries) == 1
    query, params = db.queries[0]
    assert "OR 1=1" not in query and "account" not in query
    assert "SELECT *" not in query
    assert params[-1] == 149


def test_species_names_are_copied_and_sanitized():
    names = {"149": "Dragonite", "bad": "Ignored", "42": "Gol\nbat"}
    context = PublicMapContext(FakeDB(), names)
    assert context._by_id == {149: "Dragonite"}
    names["149"] = "HACKED"
    assert context._by_id[149] == "Dragonite"


@pytest.mark.parametrize("text", [None, "", "x" * 2001, 123])
async def test_invalid_question_returns_no_snapshot(text):
    db = FakeDB()
    context = PublicMapContext(db, NAMES)
    assert await context.snapshot(text) is None
    assert db.queries == []
