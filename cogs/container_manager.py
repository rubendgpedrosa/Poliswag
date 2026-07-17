import discord
from discord.ext import commands

from modules.config import Config


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
        msg = await ctx.send("⏳ A recolher dados…")
        try:
            data = await self.poliswag.scanner_status.get_full_status()
        except Exception as e:
            await msg.edit(content=f"❌ Erro ao recolher estado: {e}")
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

        embed = discord.Embed(title="Scanner — Estado", color=Config.EMBED_COLOR)
        embed.add_field(name="Pokémon (Golbat)", value=pokemon_line, inline=False)
        embed.add_field(name="Dispositivos (Rotom)", value=devices_text, inline=False)
        embed.add_field(name="Workers (Dragonite)", value=workers_text, inline=False)
        embed.add_field(name="Contas (Dragonite)", value=accounts_text, inline=False)

        await msg.edit(content=None, embed=embed)

    @commands.group(name="container", invoke_without_command=True)
    async def container(self, ctx):
        await ctx.send(
            "Invalid container command. Use `container start`, `container stop`, "
            "`container recreate` or `container autorecreate on|off`."
        )

    @container.command(name="start")
    async def start_container(self, ctx):
        await ctx.send(
            f"Attempting to start container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("start")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' start command sent."
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): started '{self.SCANNER_CONTAINER_NAME}'"
            )
        except Exception as e:
            error_message = f"Error starting container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await ctx.send(error_message)

    @container.command(name="stop")
    async def stop_container(self, ctx):
        await ctx.send(
            f"Attempting to stop container '{self.SCANNER_CONTAINER_NAME}'..."
        )
        try:
            self.poliswag.scanner_manager.change_scanner_status("stop")
            await ctx.send(
                f"Container '{self.SCANNER_CONTAINER_NAME}' stop command sent."
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): stopped '{self.SCANNER_CONTAINER_NAME}'"
            )
        except Exception as e:
            error_message = f"Error stopping container: {e}"
            print(error_message)
            self.poliswag.utility.log_to_file(error_message, "ERROR")
            await ctx.send(error_message)

    @container.command(name="recreate")
    async def recreate_containers(self, ctx):
        msg = await ctx.send(f"⏳ A recriar containers `{Config.RECREATE_SERVICES}`…")
        ok = await self.poliswag.stack_recovery.recreate_services()
        if ok:
            await msg.edit(
                content=f"✅ Containers `{Config.RECREATE_SERVICES}` recriados."
            )
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): recreated "
                f"'{Config.RECREATE_SERVICES}'"
            )
        else:
            await msg.edit(content="❌ Falha ao recriar containers. Verifica os logs.")
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): recreate FAILED",
                "ERROR",
            )

    @container.command(name="autorecreate")
    async def container_autorecreate(self, ctx, state: str = None):
        if state is None:
            current = self.poliswag.stack_recovery.auto_recreate_enabled
            status = "activada 🟢" if current else "desactivada 🔴"
            await ctx.send(f"Recriação automática: **{status}**. Usa `on` ou `off`.")
            return
        state = state.lower()
        if state in ("on", "enable", "1", "true"):
            self.poliswag.stack_recovery.auto_recreate_enabled = True
            await ctx.send("✅ Recriação automática de containers **activada**.")
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): auto-recreate ENABLED"
            )
        elif state in ("off", "disable", "0", "false"):
            self.poliswag.stack_recovery.auto_recreate_enabled = False
            await ctx.send("🔕 Recriação automática de containers **desactivada**.")
            self.poliswag.utility.log_to_file(
                f"[CONTAINER] @{ctx.author} ({ctx.author.id}): auto-recreate DISABLED"
            )
        else:
            await ctx.send("Estado inválido. Usa `on` ou `off`.")

    # ---- !device command group --------------------------------------------

    @commands.group(name="device", invoke_without_command=True)
    async def device(self, ctx):
        await ctx.send(
            "`!device status` — verifica ligação ADB\n"
            "`!device logcat [linhas]` — últimas N linhas filtradas por aegis/poke (padrão 10)\n"
            "`!device autoreboot on|off` — activa/desactiva reboot automático\n"
            "`!device restartapp` — reinicia a app Pokémon GO via ADB\n"
            "`!device reboot` — reinicia o dispositivo via ADB"
        )

    @device.command(name="restartapp", brief="Reinicia a app Pokémon GO via ADB")
    async def device_restartapp(self, ctx):
        msg = await ctx.send("⏳ A reiniciar a app…")
        ok = await self.poliswag.device_manager.restart_app()
        if ok:
            await msg.edit(content="✅ App Pokémon GO reiniciada via ADB.")
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual app restart sent"
            )
        else:
            await msg.edit(
                content="❌ Falha ao reiniciar a app. Verifica `!device status`."
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual app restart FAILED",
                "ERROR",
            )

    @device.command(name="status", brief="Verifica ligação ADB ao dispositivo")
    async def device_status(self, ctx):
        msg = await ctx.send("⏳ A verificar ligação ADB…")
        dm = self.poliswag.device_manager
        model = await dm.get_model()
        device = Config.ADB_DEVICE
        if model:
            embed = discord.Embed(
                title="ADB — Dispositivo ligado",
                description=f"**Endereço:** `{device}`\n**Modelo:** `{model}`",
                color=discord.Color.green(),
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): checked ADB status → connected ({model})"
            )
        else:
            embed = discord.Embed(
                title="ADB — Sem ligação",
                description=f"**Endereço:** `{device or '(não configurado)'}`",
                color=discord.Color.red(),
            )
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): checked ADB status → no response from {device}"
            )
        await msg.edit(content=None, embed=embed)

    @device.command(name="logcat", brief="Últimas N linhas de logcat com aegis/poke")
    async def device_logcat(self, ctx, lines: int = 10):
        if lines < 1 or lines > 200:
            await ctx.send("❌ Número de linhas deve estar entre 1 e 200.")
            return
        msg = await ctx.send("⏳ A obter logcat…")
        self.poliswag.utility.log_to_file(
            f"[DEVICE] @{ctx.author} ({ctx.author.id}): requested last {lines} logcat lines (aegis/poke filter)"
        )
        output = await self.poliswag.device_manager.logcat_filtered(lines)
        if len(output) > 1900:
            output = "…" + output[-1897:]
        await msg.edit(content=f"```\n{output}\n```")

    @device.command(name="autoreboot", brief="Activa ou desactiva o reboot automático")
    async def device_autoreboot(self, ctx, state: str):
        state = state.lower()
        if state in ("on", "enable", "1", "true"):
            self.poliswag.device_manager.auto_reboot_enabled = True
            await ctx.send("✅ Reboot automático **activado**.")
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): auto-reboot ENABLED"
            )
        elif state in ("off", "disable", "0", "false"):
            self.poliswag.device_manager.auto_reboot_enabled = False
            await ctx.send("🔕 Reboot automático **desactivado**.")
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): auto-reboot DISABLED"
            )
        else:
            current = self.poliswag.device_manager.auto_reboot_enabled
            status = "activado 🟢" if current else "desactivado 🔴"
            await ctx.send(f"Estado actual: **{status}**. Usa `on` ou `off`.")

    @device.command(name="reboot", brief="Reinicia o dispositivo via ADB")
    async def device_reboot(self, ctx):
        msg = await ctx.send("⏳ A enviar comando de reboot…")
        ok = await self.poliswag.device_manager.reboot()
        if ok:
            await msg.edit(content=f"✅ Reboot enviado para `{Config.ADB_DEVICE}`.")
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual ADB reboot sent to {Config.ADB_DEVICE}"
            )
        else:
            await msg.edit(content="❌ Falha no reboot. Verifica `!device status`.")
            self.poliswag.utility.log_to_file(
                f"[DEVICE] @{ctx.author} ({ctx.author.id}): manual ADB reboot FAILED",
                "ERROR",
            )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You are not authorized to use this command.")
            return
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(
                "Invalid container command. Use `container start` or `container stop`."
            )
            return
        error_message = f"An error occurred: {error}"
        print(error_message)
        self.poliswag.utility.log_to_file(error_message, "ERROR")
        await ctx.send(error_message)


async def setup(bot):
    await bot.add_cog(ContainerManagerCog(bot))
