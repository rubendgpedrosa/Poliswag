#!/usr/bin/env python3
"""Preview operational notices with simulated events; optionally DM the owner."""

# ruff: noqa: E402 -- Add the repository paths before importing its modules.

import argparse
import asyncio
import copy
import datetime
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dm_owner
from test_scanner_update import StagingTests
from modules.config import Config
from modules.device_manager import DeviceManager
from modules.owner_alert import notify_owner
from modules.stack_recovery import StackRecovery
from cogs.webstats import WebStats


def load_script(name):
    loader = importlib.machinery.SourceFileLoader(
        name.replace("-", "_"), str(ROOT / "scripts" / name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def host_payload(command):
    assert command[:2] == ["/usr/bin/python3", str(ROOT / "scripts/dm_owner.py")]
    return dm_owner.embed(command[2], command[3])


def update_examples(state):
    examples = []
    for label, failed, empty in [
        (
            "Atualizações disponíveis: serviços, bases de dados e instalação manual",
            False,
            False,
        ),
        ("Atualizações disponíveis com uma verificação incompleta", True, False),
        ("Falha na verificação sem atualizações confirmadas", True, True),
    ]:
        fixture = StagingTests()
        fixture.setUp()
        try:
            saved = copy.deepcopy(state)
            saved.pop("notified_signature", None)
            if empty:
                saved["services"] = {}
            else:
                saved["services"]["tileserver-cache"]["pending"] = True
            (fixture.state_dir / "state.json").write_text(json.dumps(saved))
            after = (
                dict(fixture.before, State={"StartedAt": "TESTE"}) if failed else None
            )
            _, operations = fixture.main_with_registry(["--notify"], after=after)
            notice = next(cmd for cmd in operations if cmd[0] == "/usr/bin/python3")
            examples.append((label, host_payload(notice)))
        finally:
            fixture.doCleanups()
    return examples


def database_examples(state):
    hook = load_script("scanner-db-shutdown")
    examples = []
    for label, failures in [
        ("Bases de dados: ambas as atualizações concluídas", set()),
        ("Bases de dados: uma atualização concluída e outra adiada", {"diadem-db"}),
        (
            "Bases de dados: atualizações adiadas por falha na cópia de segurança",
            {"db", "diadem-db"},
        ),
    ]:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            saved = {
                "services": {
                    name: copy.deepcopy(state["services"][name])
                    for name in hook.DATABASES
                }
            }
            for record in saved["services"].values():
                record.update(pending=True, downloaded=True)
            (state_dir / "state.json").write_text(json.dumps(saved))
            commands = []

            def apply(name, record):
                if name in failures:
                    raise RuntimeError(
                        "A cópia de segurança ficou incompleta. A atualização foi adiada."
                    )
                return {
                    "Image": "TESTE",
                    "Config": {"Image": record["candidate"]},
                }, "/teste/copia.sql.gz"

            with patch.object(hook, "STATE_DIR", state_dir), patch.object(
                hook, "shutting_down", return_value=True
            ), patch.object(hook, "apply_database", side_effect=apply), patch.object(
                hook,
                "run",
                side_effect=lambda command, **kwargs: commands.append(command) or "",
            ), patch.object(
                sys, "argv", ["scanner-db-shutdown", "--shutdown"]
            ):
                hook.main()
            assert len(commands) == 1
            examples.append((label, host_payload(commands[0])))
    return examples


def diadem_examples():
    source = Path("/usr/local/sbin/diadem-update").read_text()
    # Execute ONLY message-formatting statements, never the updater itself.
    capture = "import json,sys; print(json.dumps({'title':sys.argv[1],'description':sys.argv[2]},ensure_ascii=False))"
    prefix = f'dm() {{ /usr/bin/python3 -c {shlex.quote(capture)} "$1" "$2"; }}\nlog() {{ :; }}\ngit() {{ echo "$PREVIEW_REV"; }}\n'
    examples = []
    revision = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd="/opt/diadem", text=True
    ).strip()
    subjects = subprocess.check_output(
        [
            "git",
            "log",
            "-7",
            "--format=• [%s](https://github.com/ccev/diadem/commit/%h)",
            "origin/main",
        ],
        cwd="/opt/diadem",
        text=True,
    ).splitlines()
    success = source[source.rindex('dm "✅ Mapa (Diadem) atualizado"') :]
    plurals = re.search(r'alteracoes="alterações"\n[^\n]+', source).group()
    extra = next(
        line for line in source.splitlines() if line.startswith('[ "$behind" -gt 5 ]')
    )
    env = {
        **os.environ,
        "PREVIEW_REV": revision,
        "REPO": "/opt/diadem",
        "BRANCH": "pogoleiria",
    }
    for count in (1, 7):
        result = subprocess.run(
            ["bash", "-c", prefix + plurals + "\n" + extra + "\n" + success],
            env={
                **env,
                "behind": str(count),
                "subjects": "\n".join(subjects[: min(count, 5)]),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        body = json.loads(result.stdout.splitlines()[-1])
        examples.append(
            (
                f"Diadem: atualização com {count} "
                + ("alteração" if count == 1 else "alterações"),
                dm_owner.embed(body["description"], body["title"]),
            )
        )
    failure = (
        "fail() {\n"
        + re.search(r"fail\(\) \{\n(.*?)\n\}", source, re.S).group(1)
        + "\n}\n"
    )
    reasons = [
        "pasta inexistente",
        "ramo incorreto",
        "alterações locais",
        "conflito de integração",
        "falha ao criar a imagem",
        "reposição da versão anterior",
    ]
    calls = re.findall(r'\bfail ("[^\n]+")', source)
    assert len(calls) == len(reasons)
    for label, call in zip(reasons, calls):
        result = subprocess.run(
            ["bash", "-c", prefix + failure + "fail " + call],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        body = json.loads(result.stdout.splitlines()[-1])
        examples.append(
            ("Diadem: " + label, dm_owner.embed(body["description"], body["title"]))
        )
    return examples


def host_fallback_examples():
    capture = "import json,sys; print(json.dumps({'title':sys.argv[1],'description':sys.argv[2]},ensure_ascii=False))"
    prefix = f'python3() {{ /usr/bin/python3 -c {shlex.quote(capture)} "$3" "$2"; }}\nlog() {{ :; }}\nnow() {{ :; }}\n'
    watchdog = (ROOT / "scripts/watchdog.sh").read_text()
    command = watchdog[watchdog.index('python3 "$ROOT/scripts/notify_owner.py"') :]
    result = subprocess.run(
        ["bash", "-c", prefix + command],
        env={**os.environ, "ROOT": str(ROOT), "age": "900"},
        capture_output=True,
        text=True,
        check=True,
    )
    notice = json.loads(result.stdout.splitlines()[-1])
    examples = [
        (
            "Aviso do host: Poliswag reiniciado pelo watchdog",
            dm_owner.embed(notice["description"], notice["title"]),
        )
    ]
    backup_source = Path("/root/db-backup.sh").read_text()
    failure = (
        "fail() {\n"
        + re.search(r"fail\(\) \{\n(.*?)\n\}", backup_source, re.S).group(1)
        + "\n}\n"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            prefix + failure + 'fail "Não foi possível concluir a cópia de segurança."',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    notice = json.loads(result.stdout.splitlines()[-1])
    examples.append(
        (
            "Aviso do host: falha na cópia de segurança",
            dm_owner.embed(notice["description"], notice["title"]),
        )
    )
    return examples


async def bot_examples():
    examples = []
    label = ""

    async def capture(*, embed):
        examples.append((label, embed.to_dict()))

    bot = MagicMock()
    bot.MOD_CHANNEL.send = AsyncMock(side_effect=capture)
    recovery = StackRecovery(bot)
    recovery._recovery_attempts = 1
    for problem, description in [
        ("phone", "problema no telemóvel"),
        ("stack", "scanner sem resposta"),
        ("link", "ligação interrompida"),
    ]:
        label = "Recuperação: primeira tentativa — " + description
        await recovery._announce(problem, 300, True)
    recovery._recovery_attempts = 2
    label = "Recuperação: última tentativa concluída"
    await recovery._announce("stack", 900, True)
    label = "Recuperação: última tentativa com erro"
    await recovery._announce("phone", 900, False)
    label = "Recuperação: sem dados, a aguardar"
    await recovery._announce_waiting("no_data", 300)
    label = "Recuperação: telemóvel inacessível"
    await recovery._announce_waiting("unreachable", 300)
    label = "Recuperação: mapa novamente disponível"
    await recovery._announce_recovered(900)
    label = "Recuperação: reinício adiado para preservar as sessões"
    with patch(
        "modules.stack_recovery.fetch_data",
        new=AsyncMock(
            side_effect=[{"in_use": 16}, {"devices": [{"worker_in_use_count": 15}]}]
        ),
    ):
        assert await recovery._sessions_allow_reset() is False
    device = DeviceManager(bot)
    device.get_auto_reboot_enabled = AsyncMock(return_value=True)
    device._offline_since = 1000
    bot.account_monitor.is_device_connected = AsyncMock(return_value=False)
    label = "Telemóvel: desligado do scanner"
    with patch("modules.device_manager.time.time", return_value=1900):
        await device.alert_if_offline()
    bot.account_monitor.is_device_connected.return_value = True
    label = "Telemóvel: ligação restabelecida"
    with patch("modules.device_manager.time.time", return_value=2200):
        await device.alert_if_offline()
    stats = WebStats(bot)
    for label, message in [
        (
            "Estatísticas: mensagens privadas desativadas",
            "Não consegui enviar as estatísticas por DM (DMs fechadas?).",
        ),
        (
            "Estatísticas: falha na mensagem privada",
            "Não consegui enviar as estatísticas por DM.",
        ),
    ]:
        await stats._warn_mods(message)
    bot.get_user.return_value.send = AsyncMock(side_effect=capture)
    label = "Aviso ao proprietário: alternativa à notificação no telemóvel"
    with patch.object(Config, "IS_PRODUCTION", False), patch.object(Config, "MY_ID", 1):
        assert await notify_owner(
            bot,
            "Não foi possível aceder ao mapa durante as últimas verificações.",
            title="Mapa indisponível",
            tag="teste",
        )
    return examples


def post_with_retry(path, token, body):
    for attempt in range(4):
        try:
            return dm_owner.post(path, token, body)
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            retry = json.load(error).get("retry_after", 2)
            time.sleep(min(float(retry), 30) + 0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send clearly labelled preview messages to MY_ID only",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "logs/operational-embed-preview.json"
    )
    args = parser.parse_args()
    print(
        "Generating simulated events only; operational commands are mocked.", flush=True
    )
    state = json.loads(Path("/var/lib/scanner-update/state.json").read_text())
    samples = (
        update_examples(state)
        + database_examples(state)
        + diadem_examples()
        + host_fallback_examples()
        + asyncio.run(bot_examples())
    )
    unique, seen = [], set()
    for label, embed in samples:
        key = (embed.get("title"), embed.get("description"), embed.get("color"))
        if key in seen:
            continue
        seen.add(key)
        assert len(embed.get("title", "")) <= 256
        assert len(embed.get("description", "")) <= 4096
        unique.append({"label": label, "embed": embed})
    report = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "samples": unique,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"Prepared {len(unique)} distinct operational embed previews.", flush=True)
    if args.send:
        env = dm_owner.read_env(dm_owner.ENV_FILE)
        token = env["DISCORD_API_KEY"]
        channel = post_with_retry(
            "/users/@me/channels", token, {"recipient_id": env["MY_ID"]}
        )
        for index, sample in enumerate(unique, 1):
            result = post_with_retry(
                f"/channels/{channel['id']}/messages",
                token,
                {
                    "content": f"**TESTE {index}/{len(unique)} — {sample['label']}**\nPré-visualização; não corresponde a um evento real.",
                    "embeds": [sample["embed"]],
                    "allowed_mentions": {"parse": []},
                },
            )
            assert result.get("embeds"), "Discord did not accept the embed"
            sample["message_id"] = result["id"]
            args.output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            )
            print(f"Sent {index}/{len(unique)}: {sample['label']}", flush=True)
            time.sleep(0.7)


if __name__ == "__main__":
    main()
