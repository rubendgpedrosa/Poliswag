from types import SimpleNamespace
from unittest.mock import AsyncMock


async def test_catalogue_refresh_includes_reviewed_costumes_before_marking_success(
    monkeypatch,
):
    import cogs.scheduled as scheduled

    names, costumes = AsyncMock(), AsyncMock()
    monkeypatch.setattr(scheduled, "sync_pokemon_names", names)
    monkeypatch.setattr(scheduled, "sync_pokemon_costumes", costumes, raising=False)
    masterfile = {"pokemon": {"25": {"name": "Pikachu"}}}
    db = object()
    cog = SimpleNamespace(
        poliswag=SimpleNamespace(
            quest_search=SimpleNamespace(masterfile_data=masterfile), db=db
        ),
        _pokemon_names_synced=False,
    )
    await scheduled.Scheduled._sync_pokemon_names(cog)
    costumes.assert_awaited_once_with(db, masterfile)
    names.assert_awaited_once_with(db, masterfile)
    assert cog._pokemon_names_synced is True
