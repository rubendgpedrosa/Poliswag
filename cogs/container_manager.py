import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import status_embed


class ContainerManagerCog(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCANNER_CONTAINER_NAME = Config.SCANNER_CONTAINER_NAME

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return ctx.author.id == Config.MY_ID

    @commands.command(
        name="status",
        brief="Diagnóstico ao vivo do scanner (admin)",
        help=(
            "Mostra o estado em tempo real de todos os componentes do scanner:\n"
            "  • Último pokémon visto (golbat DB)\n"
            "  • Dispositivos Rotom (isAlive, último heartbeat)\n"
            "  • Workers Dragonite (estado, última data)\n"
            "  • Pool de contas (boas / em uso / cooldown / desactivadas)"
        ),
    )
    async def status_cmd(self, ctx):
        msg = await ctx.send(embed=status_embed("⏳ A recolher dados…"))
        try:
            data = await self.poliswag.scanner_status.get_full_status()
        except Exception as e:
            self.poliswag.utility.log_to_file(f"[DEVICE] status_cmd failed: {e}")
            await msg.edit(
                embed=status_embed(
                    f"❌ Erro ao recolher estado: {e}", color=discord.Color.red()
                )
            )
            return

        # ── Pokemon freshness ────────────────────────────────────────────────
        secs = data["last_pokemon_seconds_ago"]
        if secs is None:
            pokemon_line = "❓ Desconhecido"
        elif secs < 600:
            pokemon_line = f"🟢 Último pokémon há **{secs}s**"
        else:
            pokemon_line = f"🔴 Último pokémon há **{secs}s** — STALE"

        # ── Rotom devices ────────────────────────────────────────────────────
        device_lines = []
        for d in data["devices"]:
            icon = "🟢" if d["is_alive"] else "🔴"
            age = d["last_msg_seconds_ago"]
            age_str = f"{age}s atrás" if age is not None else "?"
            device_lines.append(f"{icon} **{d['origin']}** — último msg {age_str}")
        devices_text = "\n".join(device_lines) or "_sem dispositivos_"

        # ── Dragonite workers ────────────────────────────────────────────────
        worker_lines = []
        for w in data["workers"]:
            age = w["last_data_seconds_ago"]
            icon = "🟢" if (age is not None and age < 600) else "🔴"
            age_str = f"{age}s" if age is not None else "?"
            worker_lines.append(
                f"{icon} `{w['worker_id']}` ({w['area']}) — {w['status']}, data {age_str} atrás"
            )
        workers_text = "\n".join(worker_lines) or "_sem workers_"

        # ── Accounts ─────────────────────────────────────────────────────────
        acc = data["accounts"]
        accounts_text = (
            f"✅ Boas: **{acc.get('good', 0)}**  "
            f"🔄 Em uso: **{acc.get('in_use', 0)}**  "
            f"⏳ Cooldown: **{acc.get('cooldown', 0)}**  "
            f"❌ Desactivadas: **{acc.get('disabled', 0)}**"
        )

        embed = status_embed("Scanner — Estado")
        embed.add_field(name="Pokémon (Golbat)", value=pokemon_line, inline=False)
        embed.add_field(name="Dispositivos (Rotom)", value=devices_text, inline=False)
        embed.add_field(name="Workers (Dragonite)", value=workers_text, inline=False)
        embed.add_field(name="Contas (Dragonite)", value=accounts_text, inline=False)

        await msg.edit(embed=embed)

    @commands.group(
        name="container",
        invoke_without_command=True,
        brief="Gere o container do scanner (admin)",
    )
    async def container(self, ctx):
        await ctx.send(
            embed=status_embed(
                "Comando inválido",
                "Usa `container start`, `container stop`, `container recreate` "
                "ou `container autorecreate on|off`.",
            )
        )

    @container.command(name="start", brief="Inicia o container do scanner (admin)")
    async def start_container(self, ctx):
        msg = await ctx.send(
            embed=status_embed(
                f"⏳ A iniciar o container '{self.SCANNER_CONTAINER_NAME}'…"
            )
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("start")
            await msg.edit(
                embed=status_embed(
                    f"✅ Comando de início enviado para '{self.SCANNER_CONTAINER_NAME}'.",
                    color=discord.Color.green(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): started '{self.SCANNER_CONTAINER_NAME}'"
            )
        except Exception as e:
            error_message = f"Erro ao iniciar o container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await msg.edit(
                embed=status_embed(f"❌ {error_message}", color=discord.Color.red())
            )

    @container.command(name="stop", brief="Pára o container do scanner (admin)")
    async def stop_container(self, ctx):
        msg = await ctx.send(
            embed=status_embed(
                f"⏳ A parar o container '{self.SCANNER_CONTAINER_NAME}'…"
            )
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("stop")
            await msg.edit(
                embed=status_embed(
                    f"✅ Comando de paragem enviado para '{self.SCANNER_CONTAINER_NAME}'.",
                    color=discord.Color.green(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): stopped '{self.SCANNER_CONTAINER_NAME}'"
            )
        except Exception as e:
            error_message = f"Erro ao parar o container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await msg.edit(
                embed=status_embed(f"❌ {error_message}", color=discord.Color.red())
            )

    @container.command(
        name="recreate",
        brief="Recria manualmente os containers do scanner (admin)",
    )
    async def recreate_containers(self, ctx):
        msg = await ctx.send(
            embed=status_embed(f"⏳ A recriar containers `{Config.RECREATE_SERVICES}`…")
        )
        ok = await self.poliswag.stack_recovery.recreate_services()
        if ok:
            await msg.edit(
                embed=status_embed(
                    f"✅ Containers `{Config.RECREATE_SERVICES}` recriados.",
                    color=discord.Color.green(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): recreated "
                f"'{Config.RECREATE_SERVICES}'"
            )
        else:
            await msg.edit(
                embed=status_embed(
                    "❌ Falha ao recriar containers. Verifica os logs.",
                    color=discord.Color.red(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): recreate FAILED",
                "ERROR",
            )

    @container.command(
        name="autorecreate",
        brief="Activa/desactiva a recriação automática dos containers (admin)",
    )
    async def container_autorecreate(self, ctx, state: str = None):
        if state is None:
            current = await self.poliswag.stack_recovery.get_auto_recreate_enabled()
            status = "activada 🟢" if current else "desactivada 🔴"
            await ctx.send(
                embed=status_embed(
                    f"Recriação automática: {status}", "Usa `on` ou `off`."
                )
            )
            return
        state = state.lower()
        if state in ("on", "enable", "1", "true"):
            await self.poliswag.stack_recovery.set_auto_recreate_enabled(True)
            await ctx.send(
                embed=status_embed(
                    "✅ Recriação automática de containers activada.",
                    color=discord.Color.green(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): auto-recreate ENABLED"
            )
        elif state in ("off", "disable", "0", "false"):
            await self.poliswag.stack_recovery.set_auto_recreate_enabled(False)
            await ctx.send(
                embed=status_embed("🔕 Recriação automática de containers desactivada.")
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): auto-recreate DISABLED"
            )
        else:
            await ctx.send(
                embed=status_embed(
                    "Estado inválido. Usa `on` ou `off`.", color=discord.Color.red()
                )
            )

    # ---- !device command group --------------------------------------------

    @commands.group(
        name="device",
        invoke_without_command=True,
        brief="Gere o dispositivo Pokémon GO via ADB (admin)",
    )
    async def device(self, ctx):
        await ctx.send(
            embed=status_embed(
                "Comandos de dispositivo",
                "`!device status` — verifica ligação ADB\n"
                "`!device logcat [linhas]` — últimas N linhas filtradas por "
                "aegis/poke (padrão 10)\n"
                "`!device autoreboot on|off` — activa/desactiva reboot automático\n"
                "`!device restartapp` — reinicia a app Pokémon GO via ADB\n"
                "`!device reboot` — reinicia o dispositivo via ADB",
            )
        )

    @device.command(name="restartapp", brief="Reinicia a app Pokémon GO via ADB")
    async def device_restartapp(self, ctx):
        msg = await ctx.send(embed=status_embed("⏳ A reiniciar a app…"))
        ok = await self.poliswag.device_manager.restart_app()
        if ok:
            await msg.edit(
                embed=status_embed(
                    "✅ App Pokémon GO reiniciada via ADB.", color=discord.Color.green()
                )
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual app restart sent"
            )
        else:
            await msg.edit(
                embed=status_embed(
                    "❌ Falha ao reiniciar a app. Verifica `!device status`.",
                    color=discord.Color.red(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual app restart FAILED",
                "ERROR",
            )

    @device.command(name="status", brief="Verifica ligação ADB ao dispositivo")
    async def device_status(self, ctx):
        msg = await ctx.send(embed=status_embed("⏳ A verificar ligação ADB…"))
        dm = self.poliswag.device_manager
        model = await dm.get_model()
        device = Config.ADB_DEVICE
        if model:
            embed = status_embed(
                "ADB — Dispositivo ligado",
                f"**Endereço:** `{device}`\n**Modelo:** `{model}`",
                color=discord.Color.green(),
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): checked ADB status → connected ({model})"
            )
        else:
            embed = status_embed(
                "ADB — Sem ligação",
                f"**Endereço:** `{device or '(não configurado)'}`",
                color=discord.Color.red(),
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): checked ADB status → no response from {device}"
            )
        await msg.edit(embed=embed)

    @device.command(name="logcat", brief="Últimas N linhas de logcat com aegis/poke")
    async def device_logcat(self, ctx, lines: int = 10):
        if lines < 1 or lines > 200:
            await ctx.send(
                embed=status_embed(
                    "❌ Número de linhas deve estar entre 1 e 200.",
                    color=discord.Color.red(),
                )
            )
            return
        msg = await ctx.send(embed=status_embed("⏳ A obter logcat…"))
        self.poliswag.utility.log_to_file(
            f"[DEVICE] @{ctx.author} ({ctx.author.id}): requested last {lines} logcat lines (aegis/poke filter)"
        )
        output = await self.poliswag.device_manager.logcat_filtered(lines)
        if len(output) > 1900:
            output = "…" + output[-1897:]
        await msg.edit(embed=status_embed("Logcat", f"```\n{output}\n```"))

    @device.command(name="autoreboot", brief="Activa ou desactiva o reboot automático")
    async def device_autoreboot(self, ctx, state: str):
        state = state.lower()
        if state in ("on", "enable", "1", "true"):
            await self.poliswag.device_manager.set_auto_reboot_enabled(True)
            await ctx.send(
                embed=status_embed(
                    "✅ Reboot automático activado.", color=discord.Color.green()
                )
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): auto-reboot ENABLED"
            )
        elif state in ("off", "disable", "0", "false"):
            await self.poliswag.device_manager.set_auto_reboot_enabled(False)
            await ctx.send(embed=status_embed("🔕 Reboot automático desactivado."))
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): auto-reboot DISABLED"
            )
        else:
            current = await self.poliswag.device_manager.get_auto_reboot_enabled()
            status = "activado 🟢" if current else "desactivado 🔴"
            await ctx.send(
                embed=status_embed(f"Estado actual: {status}", "Usa `on` ou `off`.")
            )

    @device.command(name="reboot", brief="Reinicia o dispositivo via ADB")
    async def device_reboot(self, ctx):
        msg = await ctx.send(embed=status_embed("⏳ A enviar comando de reboot…"))
        ok = await self.poliswag.device_manager.reboot()
        if ok:
            await msg.edit(
                embed=status_embed(
                    f"✅ Reboot enviado para `{Config.ADB_DEVICE}`.",
                    color=discord.Color.green(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual ADB reboot sent to {Config.ADB_DEVICE}"
            )
        else:
            await msg.edit(
                embed=status_embed(
                    "❌ Falha no reboot. Verifica `!device status`.",
                    color=discord.Color.red(),
                )
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual ADB reboot FAILED",
                "ERROR",
            )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                embed=status_embed(
                    "❌ Não tens autorização para usar este comando.",
                    color=discord.Color.red(),
                )
            )
            return
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(
                embed=status_embed(
                    "Comando inválido", "Usa `container start` ou `container stop`."
                )
            )
            return
        error_message = f"Ocorreu um erro: {error}"
        print(error_message)
        self.poliswag.utility.log_to_file(error_message, "ERROR")
        await ctx.send(
            embed=status_embed(f"❌ {error_message}", color=discord.Color.red())
        )


async def setup(bot):
    await bot.add_cog(ContainerManagerCog(bot))
