"""Tests for cogs.lures.Lures.

The cog uses poliswag.lure_manager (a MagicMock here) and its own PoliSnatch
client/store/poller, all replaced with fakes so no HTTP or database is touched.
build_embed is patched at the import site inside cogs.lures so we assert on the
args, not discord.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.lures import Lures, setup
from modules.polisnatch_api import JobApiError


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.lure_manager = AsyncMock()
    poliswag.get_channel = MagicMock(return_value=None)
    cog = Lures(poliswag)
    cog.jobs = MagicMock()
    cog.jobs.submit_lure = AsyncMock()
    cog.jobs.workers = AsyncMock()
    cog.jobs.command = AsyncMock()
    cog.jobs.issue_token = AsyncMock()
    cog.job_store = MagicMock()
    cog.poller.start = MagicMock()
    cog.poller.stop = AsyncMock()
    cog.poller.wake = MagicMock()
    return cog


def make_ctx(author_id="42", channel_id=9):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.name = "tester"
    ctx.channel.id = channel_id
    ctx.send = AsyncMock(return_value=SimpleNamespace(id=555))
    return ctx


def make_dm_ctx(author_id="111"):
    ctx = make_ctx(author_id)
    ctx.guild = None
    return ctx


def free_stop():
    return {"locked_for_s": 0, "cooldown_for_s": 0}


class TestCogCheck:
    def test_admin_passes(self, cog):
        assert cog.cog_check(make_ctx()) is True

    def test_non_admin_fails(self, cog):
        assert cog.cog_check(make_ctx(author_id="nope")) is False


class TestLures:
    async def test_empty_sends_no_accounts_message(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = []
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        ctx.send.assert_awaited_once_with(embed="EMBED")
        title, desc = be.call_args.args[0], be.call_args.args[1]
        assert "DISPONÍVEIS" in title
        assert "Não há contas" in desc

    async def test_lists_accounts_with_credentials_and_counts(self, cog):
        ctx = make_ctx()
        cog.lure_manager.list_available_with_lures.return_value = [
            {"username": "free_low", "password": "pw_low", "nb_lures": 2},
            {"username": "free_mid", "password": "pw_mid", "nb_lures": 7},
        ]
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.lures.callback(cog, ctx)
        desc = be.call_args.args[1]
        assert "free_low / pw_low — 2 lures" in desc
        assert "free_mid / pw_mid — 7 lures" in desc
        ctx.send.assert_awaited_once_with(embed="EMBED")


class TestUseLure:
    async def test_missing_args_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username=None, number=None)
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "Utilização" in ctx.send.call_args.kwargs["embed"].description

    async def test_non_integer_number_sends_usage(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username="free_low", number="abc")
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "inteiro" in ctx.send.call_args.kwargs["embed"].description

    async def test_unknown_username_reports_not_found(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 0
        await Lures.uselure.callback(cog, ctx, username="ghost", number="-2")
        assert "não foi encontrada" in ctx.send.call_args.kwargs["embed"].description

    async def test_remove_lures_success_logs_and_confirms(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="-3")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", -3)
        cog.poliswag.utility.log_to_file.assert_called_once()
        desc = be.call_args.args[1]
        assert "3 lures removidas" in desc
        assert "free_low" in desc

    async def test_add_single_lure_uses_singular(self, cog):
        ctx = make_ctx()
        cog.lure_manager.adjust_lure_count.return_value = 1
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.uselure.callback(cog, ctx, username="free_low", number="1")
        cog.lure_manager.adjust_lure_count.assert_called_once_with("free_low", 1)
        desc = be.call_args.args[1]
        assert "1 lure adicionada" in desc

    async def test_zero_delta_sends_error_and_skips_adjust(self, cog):
        ctx = make_ctx()
        await Lures.uselure.callback(cog, ctx, username="free_low", number="0")
        cog.lure_manager.adjust_lure_count.assert_not_called()
        assert "diferente de zero" in ctx.send.call_args.kwargs["embed"].description


class TestPn:
    async def test_renders_each_worker(self, cog):
        ctx = make_ctx()
        cog.jobs.workers.return_value = {
            "worker-1": {
                "connected": True,
                "mode": "direct",
                "ticks": 3,
                "rpc_total": 120,
                "reconnect_attempts": 1,
                "receivedAt": time.time(),
            }
        }
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx)
        cog.jobs.workers.assert_awaited_once_with()
        desc = be.call_args.args[1]
        assert "**worker-1**" in desc
        assert "ligado" in desc
        assert "3 ticks" in desc
        assert "1 tentativa(s) de reconexão" in desc

    async def test_no_workers_reports_no_state(self, cog):
        ctx = make_ctx()
        cog.jobs.workers.return_value = {}
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx)
        assert be.call_args.args[0] == "SEM ESTADO"

    async def test_disabled_jobs_are_explained(self, cog):
        ctx = make_ctx()
        cog.jobs.workers.side_effect = JobApiError(
            "jobs are not enabled", status=503, code="jobs_disabled"
        )
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx)
        assert be.call_args.args[0] == "POLISNATCH INACESSÍVEL"
        assert "desativados" in be.call_args.args[1]

    async def test_a_missing_key_is_explained(self, cog):
        ctx = make_ctx()
        cog.jobs.workers.side_effect = JobApiError("no key", code="no_key")
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx)
        assert "chave de submissão" in be.call_args.args[1]

    async def test_sends_a_worker_command(self, cog):
        ctx = make_ctx()
        cog.jobs.command.return_value = {"queued": "restart", "worker": "*"}
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx, action="restart", worker="*")
        cog.jobs.command.assert_awaited_once_with("*", "restart")
        assert be.call_args.args[0] == "COMANDO ENVIADO"
        assert "todos os telefones" in be.call_args.args[1]

    async def test_unknown_action_is_rejected_before_any_call(self, cog):
        ctx = make_ctx()
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx, action="explode")
        cog.jobs.command.assert_not_called()
        assert be.call_args.args[0] == "AÇÃO DESCONHECIDA"

    async def test_a_failed_command_is_reported(self, cog):
        ctx = make_ctx()
        cog.jobs.command.side_effect = JobApiError(
            "not valid", status=401, code="unauthorized"
        )
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pn.callback(cog, ctx, action="start", worker="worker-1")
        assert be.call_args.args[0] == "COMANDO NÃO ENTREGUE"
        assert "chave" in be.call_args.args[1]


class TestPsToken:
    async def test_issues_the_token_in_the_owner_dm(self, cog):
        ctx = make_dm_ctx()
        cog.jobs.issue_token.return_value = {
            "device_id": "dev_1",
            "token": "psd_secret",
            "email": "a@b.c",
            "scope": {
                "max_workers": 2,
                "channels": ["stable"],
                "jobs": True,
            },
        }
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(
                cog, ctx, email="a@b.c", workers="2", name="Phone"
            )
        cog.jobs.issue_token.assert_awaited_once_with(
            email="a@b.c", name="Phone", workers=2
        )
        text = be.call_args.args[1]
        assert "psd_secret" in text
        assert "a@b.c" in text
        assert "Device: `dev_1`" in text
        assert "allowed_mentions" in ctx.send.call_args.kwargs
        cog.poliswag.utility.log_to_file.assert_not_called()

    async def test_a_guild_invocation_is_refused_before_any_call(self, cog):
        ctx = make_ctx()
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(cog, ctx, email="a@b.c")
        cog.jobs.issue_token.assert_not_awaited()
        assert be.call_args.args[0] == "SÓ POR DM"

    async def test_bad_email_and_worker_count_are_refused(self, cog):
        ctx = make_dm_ctx()
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(cog, ctx, email="nope")
        assert be.call_args.args[0] == "UTILIZAÇÃO INVÁLIDA"
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(cog, ctx, email="a@b.c", workers="9")
        assert be.call_args.args[0] == "UTILIZAÇÃO INVÁLIDA"
        cog.jobs.issue_token.assert_not_awaited()

    async def test_a_missing_key_is_explained(self, cog):
        ctx = make_dm_ctx()
        cog.jobs.issue_token.side_effect = JobApiError("no key", code="no_key")
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(cog, ctx, email="a@b.c")
        assert be.call_args.args[0] == "TOKEN NÃO EMITIDO"
        assert "chave" in be.call_args.args[1]

    async def test_a_reply_without_a_token_is_an_error(self, cog):
        ctx = make_dm_ctx()
        cog.jobs.issue_token.return_value = {"device_id": "dev_1"}
        with patch("cogs.lures.build_embed", return_value="EMBED") as be:
            await Lures.pstoken.callback(cog, ctx, email="a@b.c")
        assert be.call_args.args[0] == "TOKEN NÃO EMITIDO"


class TestLure:
    async def test_nearest_lure_is_submitted_tracked_and_awaited(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.return_value = {"jobId": "job_1", "state": "queued"}
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None, tipo=None)
        cog.jobs.submit_lure.assert_awaited_once_with(
            lat=None, lon=None, nearest=True, lure_type=501, requested_by="42"
        )
        assert be.call_args.args[0] == "LURE NA FILA"
        cog.job_store.record.assert_called_once_with(
            "job_1",
            "lure",
            discord_id="42",
            channel_id=9,
            message_id=555,
            stop_key="geo:nearest",
            lat=None,
            lon=None,
            nearest=True,
            state="queued",
        )
        cog.poller.wake.assert_called_once()

    async def test_coordinates_and_type_are_forwarded(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.return_value = {"jobId": "job_1", "state": "queued"}
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED"):
                await Lures.lure.callback(
                    cog, ctx, coords="39.74,-8.80", tipo="magnetic"
                )
        cog.jobs.submit_lure.assert_awaited_once_with(
            lat=39.74, lon=-8.8, nearest=False, lure_type=504, requested_by="42"
        )
        assert cog.job_store.record.call_args.kwargs["stop_key"] == "geo:39.74,-8.80"

    async def test_duplicate_follows_the_existing_job(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.return_value = {
            "jobId": "job_1",
            "state": "claimed",
            "duplicate": True,
        }
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None, tipo=None)
        assert be.call_args.args[0] == "JÁ EM CURSO"
        cog.job_store.record.assert_called_once()
        assert cog.job_store.record.call_args.kwargs["state"] == "claimed"

    async def test_a_locked_stop_is_not_submitted(self, cog):
        ctx = make_ctx()
        with patch(
            "cogs.lures.lure_locks.status",
            return_value={"locked_for_s": 120, "cooldown_for_s": 0},
        ):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords="39.74,-8.80")
        cog.jobs.submit_lure.assert_not_awaited()
        assert be.call_args.args[0] == "STOP JÁ TEM LURE"

    async def test_a_cooldown_is_not_submitted(self, cog):
        ctx = make_ctx()
        with patch(
            "cogs.lures.lure_locks.status",
            return_value={"locked_for_s": 0, "cooldown_for_s": 30},
        ):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None)
        cog.jobs.submit_lure.assert_not_awaited()
        assert be.call_args.args[0] == "PEDIDO REPETIDO"

    async def test_bad_coordinates_and_type_are_rejected(self, cog):
        ctx = make_ctx()
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords="north", tipo=None)
        assert be.call_args.args[0] == "UTILIZAÇÃO INVÁLIDA"
        cog.jobs.submit_lure.assert_not_awaited()

        ctx = make_ctx()
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None, tipo="banana")
        assert be.call_args.args[0] == "LURE DESCONHECIDO"
        cog.jobs.submit_lure.assert_not_awaited()

    async def test_disabled_jobs_are_reported(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.side_effect = JobApiError(
            "off", status=503, code="jobs_disabled"
        )
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None)
        assert be.call_args.args[0] == "POLISNATCH INDISPONÍVEL"
        assert "desativados" in be.call_args.args[1]
        cog.job_store.record.assert_not_called()

    async def test_a_job_without_an_id_is_reported(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.return_value = {"state": "queued"}
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None)
        assert be.call_args.args[0] == "RESPOSTA INESPERADA"
        cog.job_store.record.assert_not_called()

    async def test_a_tracking_failure_is_told_and_does_not_wake(self, cog):
        ctx = make_ctx()
        cog.jobs.submit_lure.return_value = {"jobId": "job_1", "state": "queued"}
        cog.job_store.record.side_effect = RuntimeError("db down")
        with patch("cogs.lures.lure_locks.status", return_value=free_stop()):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await Lures.lure.callback(cog, ctx, coords=None)
        assert [call.args[0] for call in be.call_args_list] == [
            "LURE NA FILA",
            "AVISO INDISPONÍVEL",
        ]
        cog.poller.wake.assert_not_called()
        cog.poliswag.utility.log_to_file.assert_called()


def lure_row(**overrides):
    row = {
        "job_id": "job_1",
        "kind": "lure",
        "state": "done",
        "discord_id": 42,
        "channel_id": 9,
        "message_id": 555,
        "stop_key": "geo:39.74,-8.80",
        "lat": 39.74,
        "lon": -8.8,
        "nearest": 0,
        "verified": 1,
        "fort_id": "fort-1",
        "fail_reason": None,
    }
    row.update(overrides)
    return row


def delivering_cog(cog):
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    cog.poliswag.get_channel.return_value = channel
    return channel, message


class TestAnnounce:
    async def test_a_verified_lure_edits_the_message_and_locks_the_stop(self, cog):
        _, message = delivering_cog(cog)
        with patch("cogs.lures.lure_locks.lock") as lock:
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await cog._announce_job(lure_row())
        assert be.call_args.args[0] == "LURE COLOCADO"
        assert "confirmado no mapa" in be.call_args.args[1]
        message.edit.assert_awaited_once_with(embed="EMBED")
        lock.assert_called_once_with("geo:39.74,-8.80", fort_id="fort-1", discord_id=42)

    async def test_an_unverified_lure_clears_the_lock(self, cog):
        delivering_cog(cog)
        with patch("cogs.lures.lure_locks.clear") as clear:
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await cog._announce_job(lure_row(verified=0))
        assert "não confirmado no mapa" in be.call_args.args[1]
        clear.assert_called_once_with("geo:39.74,-8.80")

    async def test_a_failure_names_the_reason(self, cog):
        delivering_cog(cog)
        with patch("cogs.lures.lure_locks.clear") as clear:
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await cog._announce_job(
                    lure_row(state="failed", verified=None, fail_reason="too_far")
                )
        assert be.call_args.args[0] == "LURE NÃO COLOCADO"
        assert be.call_args.args[1] == "o worker não chegou ao stop"
        clear.assert_called_once()

    async def test_a_job_the_server_lost_says_so(self, cog):
        delivering_cog(cog)
        with patch("cogs.lures.lure_locks.clear"):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await cog._announce_job(
                    lure_row(state="failed", verified=None, fail_reason="unknown_job")
                )
        assert "já não conhece" in be.call_args.args[1]

    async def test_a_cancelled_job_is_named(self, cog):
        delivering_cog(cog)
        with patch("cogs.lures.lure_locks.clear"):
            with patch("cogs.lures.build_embed", return_value="EMBED") as be:
                await cog._announce_job(
                    lure_row(state="cancelled", verified=None, fail_reason=None)
                )
        assert be.call_args.args[0] == "LURE NÃO COLOCADO"
        assert be.call_args.args[1] == "o job foi cancelado"

    async def test_link_jobs_are_left_to_the_linker(self, cog):
        await cog._announce_job(lure_row(kind="link"))
        cog.poliswag.get_channel.assert_not_called()

    async def test_a_deleted_message_gets_a_fresh_one(self, cog):
        channel, _ = delivering_cog(cog)
        channel.fetch_message.side_effect = discord.NotFound(
            MagicMock(status=404), "unknown message"
        )
        with patch("cogs.lures.lure_locks.lock"):
            with patch("cogs.lures.build_embed", return_value="EMBED"):
                await cog._announce_job(lure_row())
        channel.send.assert_awaited_once_with(embed="EMBED")

    async def test_a_gone_channel_is_logged_not_raised(self, cog):
        cog.poliswag.get_channel.return_value = None
        cog.poliswag.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
        with patch("cogs.lures.lure_locks.lock"):
            await cog._announce_job(lure_row())
        cog.poliswag.utility.log_to_file.assert_called()
        assert cog.poliswag.utility.log_to_file.call_args.args[1] == "ERROR"

    async def test_a_transient_discord_error_is_left_for_the_poller(self, cog):
        channel, _ = delivering_cog(cog)
        channel.fetch_message.side_effect = discord.HTTPException(
            MagicMock(status=500), "boom"
        )
        with patch("cogs.lures.lure_locks.lock"):
            with pytest.raises(discord.HTTPException):
                await cog._announce_job(lure_row())


class TestLifecycle:
    async def test_cog_load_starts_the_poller(self, cog, capsys):
        await cog.cog_load()
        cog.poller.start.assert_called_once_with()
        assert "Lures loaded" in capsys.readouterr().out

    async def test_cog_unload_stops_the_poller(self, cog, capsys):
        await cog.cog_unload()
        cog.poller.stop.assert_awaited_once_with()
        assert "Lures unloaded" in capsys.readouterr().out


class TestSetup:
    async def test_registers_cog_on_poliswag(self):
        poliswag = MagicMock()
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
        assert isinstance(poliswag.add_cog.call_args.args[0], Lures)
