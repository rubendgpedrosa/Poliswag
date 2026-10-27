from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from modules.config import Config
from modules.shiny_leaderboard import ShinyLeaderboard, render_report


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setattr(Config, "SHINY_LEADERBOARD_ENABLED", True)
    bot = SimpleNamespace(
        db=SimpleNamespace(
            get_data_from_database=AsyncMock(
                return_value=[{"last_shiny_leaderboard_date": None}]
            ),
            execute_query_to_database=AsyncMock(),
        )
    )
    report = ShinyLeaderboard(bot)
    report.send = AsyncMock()
    return report


@pytest.mark.parametrize("day,utc_hour", [("2026-10-12", 8), ("2026-10-26", 9)])
async def test_nine_lisbon_follows_dst(delivery, day, utc_hour):
    before = datetime.fromisoformat(f"{day}T{utc_hour-1:02}:59:00+00:00")
    await delivery.tick(before)
    delivery.send.assert_not_awaited()
    await delivery.tick(datetime.fromisoformat(f"{day}T{utc_hour:02}:00:00+00:00"))
    await delivery.tick(datetime.fromisoformat(f"{day}T{utc_hour:02}:01:00+00:00"))
    delivery.send.assert_awaited_once()


async def test_restart_does_not_resend_saved_day(delivery):
    delivery.poliswag.db.get_data_from_database.return_value = [
        {"last_shiny_leaderboard_date": "2026-10-13"}
    ]
    await delivery.tick(datetime(2026, 10, 13, 10, tzinfo=ZoneInfo("Europe/Lisbon")))
    delivery.send.assert_not_awaited()


async def test_failed_dm_can_retry_and_is_not_recorded(delivery):
    delivery.send.side_effect = RuntimeError("unavailable")
    now = datetime(2026, 10, 13, 9, tzinfo=ZoneInfo("Europe/Lisbon"))
    with pytest.raises(RuntimeError):
        await delivery.tick(now)
    delivery.poliswag.db.execute_query_to_database.assert_not_awaited()
    delivery.send.side_effect = None
    await delivery.tick(now)
    delivery.poliswag.db.execute_query_to_database.assert_awaited_once()


async def test_native_discord_send_only_to_owner(monkeypatch):
    from unittest.mock import Mock

    owner = SimpleNamespace(send=AsyncMock(return_value="sent"))
    bot = SimpleNamespace(get_user=Mock(return_value=owner), fetch_user=AsyncMock())
    delivery = ShinyLeaderboard(bot)
    delivery.avatars = AsyncMock(return_value={})
    monkeypatch.setattr("modules.shiny_leaderboard.render_report", lambda *args: b"png")
    assert (
        await delivery.send({"metadata": {"available": True, "complete_window": True}})
        == "sent"
    )
    bot.get_user.assert_called_once_with(Config.MY_ID)
    owner.send.assert_awaited_once()
    assert (
        owner.send.call_args.kwargs["file"].filename
        == "pogoleiria-shiny-leaderboard.png"
    )


def test_render_partial_and_missing_avatar():
    data = report_fixture(1, partial=True)
    data["trainers"][0]["trainer"] = "ExquisiteRekrad"
    with Image.open(
        BytesIO(render_report(data, {"ExquisiteRekrad": b"bad avatar"}))
    ) as image:
        assert image.format == "PNG"
        assert image.width == 1080
        assert image.height > 600


def report_fixture(count=8, partial=False):
    scores = [268, 251, 244, 230, 218, 205, 205, 187]
    rows = [
        {
            "trainer": f"Treinador{i:03}",
            "predicted_shinies": scores[i] if i < len(scores) else max(0, 180 - i),
            "shinies_per_hour": (
                round((scores[i] if i < len(scores) else max(0, 180 - i)) / 24, 3)
                if not partial
                else None
            ),
        }
        for i in range(count)
    ]
    return {
        "metadata": {
            "available": True,
            "complete_window": not partial,
            "hours": 24,
            "collection_started_utc": (
                "2026-10-14T01:00:00+00:00" if partial else "2026-10-12T08:00:00+00:00"
            ),
            "gaps": [],
            "window_start_utc": "2026-10-13T08:00:00+00:00",
            "window_end_utc": "2026-10-14T08:00:00+00:00",
        },
        "total_predicted_shinies": sum(row["predicted_shinies"] for row in rows),
        "trainers": rows,
    }


@pytest.mark.parametrize("count", [0, 1, 2, 3, 8, 100])
def test_podium_handles_small_and_large_reports(count):
    data = report_fixture(count)
    with Image.open(BytesIO(render_report(data))) as image:
        image.load()
        assert image.width == 1080
        assert image.height >= 785
        if count > 3:
            assert image.height == 747 + (min(count, 15) - 3) * 79 + 38 + 250


@pytest.fixture
def drawn_text(monkeypatch):
    from PIL import ImageDraw

    records = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        records.append((xy, str(text)))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    return records


def test_podium_preserves_totals_ties_and_local_time_without_timezone_label(drawn_text):
    render_report(report_fixture())
    texts = [content for _, content in drawn_text]
    assert [content for (x, _), content in drawn_text if x == 77] == [
        "4",
        "5",
        "6",
        "6",
        "8",
    ]
    assert "1 808" in texts
    assert "13/10 09:00 → 14/10 09:00" in texts
    assert not any(
        "Lisboa" in value or "Lisbon" in value or "UTC" in value for value in texts
    )
    assert "Previsões, não capturas confirmadas." in texts


def test_partial_history_shows_recorded_counts_over_requested_window(drawn_text):
    data = report_fixture(4, partial=True)
    assert all(row["shinies_per_hour"] is None for row in data["trainers"])
    render_report(data)
    texts = [content for _, content in drawn_text]
    assert "11,17* / hora" in texts
    assert "9,58*" in texts
    assert "/ HORA*" in texts
    assert any("Média geral: 10,34* shinies/conta/h" in value for value in texts)
    footer = " ".join(texts)
    assert "Total ÷ (4 treinadores × 24 h)." in footer
    assert "Contagens registadas ÷ horas do período pedido" in footer
    assert "podem estar subestimadas por falta de histórico" in footer
    assert any("HISTÓRICO PARCIAL · cobertura incompleta" in value for value in texts)


def test_partial_gaps_do_not_reduce_requested_denominator(drawn_text):
    data = report_fixture(4, partial=True)
    data["metadata"]["gaps"] = [
        {"started_at": 1791885600, "ended_at": 1791907200},
        {"started_at": 1791900000, "ended_at": 1791920000},
    ]
    render_report(data)
    texts = [content for _, content in drawn_text]
    assert "11,17* / hora" in texts
    assert "9,58*" in texts
    assert any("10,34*" in value for value in texts)


def test_complete_averages_include_trainer_count_in_formula(drawn_text):
    render_report(report_fixture(4))
    texts = [content for _, content in drawn_text]
    assert "11,17 / hora" in texts
    assert "9,58" in texts
    assert any("Média geral: 10,34 shinies/conta/h" in value for value in texts)
    assert "Total ÷ (4 treinadores × 24 h)." in " ".join(texts)
    assert not any("*" in value for value in texts)


def test_rate_rounds_directly_from_count_and_requested_hours(drawn_text):
    data = report_fixture(1)
    data["metadata"]["hours"] = 48
    data["trainers"][0].update(predicted_shinies=269, shinies_per_hour=5.604)
    data["total_predicted_shinies"] = 269
    render_report(data)
    texts = [content for _, content in drawn_text]
    assert "5,60 / hora" in texts
    assert any("5,60 shinies/conta/h" in value for value in texts)


def test_missing_hours_defaults_to_twenty_four(drawn_text):
    data = report_fixture(1, partial=True)
    del data["metadata"]["hours"]
    render_report(data)
    texts = [content for _, content in drawn_text]
    assert "Últimas 24 h" in texts
    assert "11,17* / hora" in texts


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(
    "hours", [0, -24, float("inf"), float("-inf"), float("nan"), None, "bad"]
)
def test_invalid_hours_show_unavailable_without_nonfinite_values(
    drawn_text, hours, partial
):
    data = report_fixture(4, partial=partial)
    data["metadata"]["hours"] = hours
    render_report(data)
    texts = [content for _, content in drawn_text]
    assert "— / hora" in texts
    assert "—" in texts
    assert any("Média geral: indisponível" in value for value in texts)
    assert "Últimas — h" in texts
    assert not any("inf" in value.lower() or "nan" in value.lower() for value in texts)


@pytest.mark.parametrize("partial", [False, True])
def test_empty_report_has_unavailable_general_average(drawn_text, partial):
    render_report(report_fixture(0, partial=partial))
    texts = [content for _, content in drawn_text]
    assert "Sem treinadores neste período." in texts
    assert "Média geral: indisponível · sem treinadores." in texts
    assert not any("Total ÷" in value for value in texts)


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("count", [0, 1, 4, 100])
def test_text_stays_inside_canvas_and_footer_content_width(monkeypatch, partial, count):
    from PIL import ImageDraw

    bounds = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, content, *args, **kwargs):
        bounds.append(
            (
                xy,
                self.textbbox(
                    xy, content, font=kwargs["font"], anchor=kwargs["anchor"]
                ),
            )
        )
        return original(self, xy, content, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    with Image.open(
        BytesIO(render_report(report_fixture(count, partial=partial)))
    ) as image:
        for (x, _), (left, top, right, bottom) in bounds:
            assert 0 <= left <= right <= image.width
            assert 0 <= top <= bottom <= image.height
            # Every left-aligned footer paragraph must honor the content margin.
            if x == 64:
                assert right <= image.width - 64


def test_long_names_and_invalid_avatars_fit_and_valid_avatars_render(drawn_text):
    data = report_fixture(4)
    for row in data["trainers"]:
        row["trainer"] = "Treinador" * 30
    avatar = BytesIO()
    Image.new("RGB", (100, 100), "red").save(avatar, format="PNG")
    png = render_report(data, {data["trainers"][0]["trainer"]: avatar.getvalue()})
    with Image.open(BytesIO(png)) as image:
        # The center podium avatar contains actual artwork.
        assert image.getpixel((540, 404)) == (255, 0, 0)
    assert any(content.endswith("…") for _, content in drawn_text)
    render_report(data, {data["trainers"][0]["trainer"]: b"invalid"})


def test_podium_ties_use_competition_ranks(drawn_text):
    data = report_fixture(3)
    data["trainers"][1]["predicted_shinies"] = data["trainers"][0]["predicted_shinies"]
    data["total_predicted_shinies"] = sum(
        row["predicted_shinies"] for row in data["trainers"]
    )
    render_report(data)
    medals = [content for _, content in drawn_text if content.endswith(".º")]
    assert medals == ["1.º", "1.º", "3.º"]


def test_top_15_limits_only_display_preserving_full_totals_and_average(drawn_text):
    import copy
    from modules.shiny_leaderboard import number

    data = report_fixture(32)
    original = copy.deepcopy(data)
    png = render_report(data)
    texts = [content for _, content in drawn_text]
    displayed = [value for value in texts if value.startswith("Treinador")]
    assert len(displayed) == 15
    assert set(displayed) == {row["trainer"] for row in data["trainers"][:15]}
    assert "Treinador015" not in texts
    assert data == original
    assert number(data["total_predicted_shinies"]) in texts
    assert "Top 15 · 32 treinadores no total" in texts
    footer = " ".join(texts)
    assert "Total ÷ (32 treinadores × 24 h)." in footer
    average = number(data["total_predicted_shinies"] / 32 / 24, 2)
    assert f"Média geral: {average} shinies/conta/h" in footer
    with Image.open(BytesIO(png)) as image:
        assert image.height == 747 + 12 * 79 + 38 + 250
