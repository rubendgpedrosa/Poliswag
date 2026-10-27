"""Durable owner runs; model credentials and conversation state live in Hermes."""

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path

import aiohttp


class HermesUnavailable(Exception):
    """No safe Hermes answer is available; never contains server error details."""


class HermesBusy(HermesUnavailable):
    """The bounded owner conversation queue is full."""


_INSTRUCTIONS = (
    "Responde em português europeu e explica decisões relevantes com clareza. "
    "Esta mensagem privada vem do dono autenticado do Poliswag. És o seu "
    "administrador de sistemas: executa o trabalho pedido dentro do âmbito "
    "autorizado, diagnostica, corrige, testa e verifica o resultado. Um pedido "
    "para resolver um problema autoriza as reparações de rotina necessárias; "
    "não peças aprovação repetida para essas tarefas. Lê as instruções AGENTS.md "
    "do projeto de destino antes de alterar ficheiros. Pede aprovação real quando "
    "necessária para ações destrutivas ou disruptivas fora do âmbito autorizado. "
    "Nunca reinicies o host Proxmox. Não reveles credenciais. Só recebes a mensagem "
    "do dono, sem anexos, histórico do Discord ou contexto de outros utilizadores. "
    "A resposta será enviada na DM de origem."
)


class HermesClient:
    POLL_INTERVAL = 1
    # Long repairs are normal; the owner sees elapsed time and can stop the run.
    RUN_TIMEOUT = 7200
    APPROVAL_TIMEOUT = 280
    # A pending record this old cannot still be running: drop it during recovery
    # so stale entries never accumulate without bound.
    MAX_RECORD_AGE = 10800
    # The server sends an SSE keepalive every 10 seconds.
    STREAM_READ_TIMEOUT = 45
    STREAM_RETRIES = 5
    STREAM_BUFFER_LIMIT = 1 << 20

    def __init__(self, url, key, owner_id, session_id="", runs_dir=None):
        self.url = url.rstrip("/")
        self.key = key
        self.conversation = f"poliswag:owner:{owner_id}"
        self.session_id = session_id
        self.runs_dir = runs_dir
        self._lock = asyncio.Lock()
        self._pending = 0
        self.active_run = None

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,200}", value
        ):
            raise HermesUnavailable()
        return value

    # --- pending-run persistence: survives a bot process restart ---

    def _record_path(self, run_id):
        return Path(self.runs_dir) / f"{run_id}.json"

    def _persist(self, run_id, channel_id, message_id):
        if not self.runs_dir or channel_id is None:
            return
        directory = Path(self.runs_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "run_id": run_id,
            "channel_id": int(channel_id),
            "message_id": int(message_id),
            "created_at": time.time(),
        }
        fd, name = tempfile.mkstemp(prefix=".run-", dir=str(directory))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(record))
            os.replace(name, self._record_path(run_id))
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def forget(self, run_id):
        if not self.runs_dir:
            return
        try:
            self._record_path(run_id).unlink(missing_ok=True)
        except OSError:
            pass

    def list_pending(self):
        if not self.runs_dir:
            return []
        directory = Path(self.runs_dir)
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            return []
        records = []
        for path in paths:
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError, TypeError):
                continue
            if (
                isinstance(record, dict)
                and isinstance(record.get("run_id"), str)
                and isinstance(record.get("channel_id"), int)
                and isinstance(record.get("message_id"), int)
                and isinstance(record.get("created_at"), (int, float))
            ):
                records.append(record)
        return records

    # --- transport ---

    @property
    def waiting(self):
        """Turns admitted to the local queue, including the active one."""
        return self._pending

    async def reply(
        self,
        text,
        *,
        message_id,
        channel_id=None,
        approval_callback=None,
        on_event=None,
    ):
        """Run one owner turn; ``on_event(dict)`` receives best-effort progress.

        Progress is ``{"event": "status", "status": ...}`` from authoritative
        polling, plus the run's own SSE events (tool calls, commentary).
        Callback failures never affect the run.
        """
        if not self.key or not self.url:
            raise HermesUnavailable()
        if self._pending >= 6:
            raise HermesBusy()
        self._pending += 1
        try:
            async with self._lock:
                return await self._request(
                    text, message_id, channel_id, approval_callback, on_event
                )
        finally:
            self._pending -= 1

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.key}",
            "X-Hermes-Session-Key": self.conversation,
        }

    async def _control(self, run_id, action, payload):
        if not self.key or not self.url:
            return False
        try:
            run_id = self._identifier(run_id)
            async with aiohttp.ClientSession(
                headers=self._headers(), trust_env=False
            ) as session:
                await self._json(
                    session, "POST", f"/v1/runs/{run_id}/{action}", payload=payload
                )
            return True
        except HermesUnavailable:
            return False

    async def stop(self, run_id):
        """Ask Hermes to interrupt a run; the poll loop reports its final state."""
        return await self._control(run_id, "stop", {})

    async def steer(self, run_id, text):
        """Inject owner guidance into a running agent without a new turn."""
        if not isinstance(text, str) or not text.strip():
            return False
        return await self._control(run_id, "steer", {"input": text.strip()[:4000]})

    async def _json(
        self, session, method, path, *, payload=None, headers=None, retries=0
    ):
        for attempt in range(retries + 1):
            try:
                async with session.request(
                    method,
                    self.url + path,
                    json=payload,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=60 if method == "POST" else 30),
                ) as response:
                    # Do not read or retain error bodies (which may contain secrets).
                    if response.status not in (200, 202):
                        raise HermesUnavailable()
                    data = await response.json()
                    if not isinstance(data, dict):
                        raise HermesUnavailable()
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == retries:
                    raise HermesUnavailable() from None
                await asyncio.sleep(self.POLL_INTERVAL)
            except (ValueError, TypeError):
                raise HermesUnavailable() from None

    async def _request(self, text, message_id, channel_id, approval_callback, on_event):
        emit = _safe_emitter(on_event)
        # Emitted before the first await, so a queued turn can still leave safely.
        emit({"event": "run.starting"})
        payload = {"input": text, "instructions": _INSTRUCTIONS}
        if self.session_id:
            payload["session_id"] = self.session_id
        headers = {**self._headers(), "Idempotency-Key": f"discord:{message_id}"}
        async with aiohttp.ClientSession(headers=headers, trust_env=False) as session:
            data = await self._json(
                session, "POST", "/v1/runs", payload=payload, retries=1
            )
            run_id = self._identifier(data.get("run_id"))
            self._persist(run_id, channel_id, message_id)
            self.active_run = run_id
            emit({"event": "run.admitted", "run_id": run_id})
            stream = None
            if on_event is not None:
                stream = asyncio.create_task(self._stream(run_id, emit))
            deadline = asyncio.get_running_loop().time() + self.RUN_TIMEOUT
            try:
                # The work may still be running server-side on failure; the
                # pending record stays so recovery can deliver it after a restart.
                answer = await self._poll(
                    session, run_id, approval_callback, deadline, emit
                )
            finally:
                self.active_run = None
                if stream is not None:
                    stream.cancel()
            return run_id, answer

    async def _stream(self, run_id, emit):
        """Relay the run's SSE events; polling stays authoritative for the result."""
        last_seq = None
        failures = 0
        timeout = aiohttp.ClientTimeout(
            total=None, sock_connect=10, sock_read=self.STREAM_READ_TIMEOUT
        )
        async with aiohttp.ClientSession(
            headers=self._headers(), trust_env=False
        ) as session:
            while failures <= self.STREAM_RETRIES:
                headers = {"Accept": "text/event-stream"}
                if last_seq is not None:
                    headers["Last-Event-ID"] = str(last_seq)
                try:
                    async with session.get(
                        f"{self.url}/v1/runs/{run_id}/events",
                        headers=headers,
                        allow_redirects=False,
                        timeout=timeout,
                    ) as response:
                        if response.status != 200:
                            return
                        buffer = b""
                        async for chunk in response.content.iter_any():
                            buffer += chunk
                            if len(buffer) > self.STREAM_BUFFER_LIMIT:
                                return
                            *frames, buffer = buffer.replace(b"\r\n", b"\n").split(
                                b"\n\n"
                            )
                            for frame in frames:
                                event = _sse_event(frame)
                                if (
                                    event is None
                                    or event.get("run_id", run_id) != run_id
                                ):
                                    continue
                                if isinstance(event.get("seq"), int):
                                    last_seq = event["seq"]
                                emit(event)
                                failures = 0
                        # A clean close means the run finished.
                        return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    failures += 1
                    await asyncio.sleep(self.POLL_INTERVAL * failures)

    async def _poll(self, session, run_id, approval_callback, deadline, emit=None):
        emit = emit or _safe_emitter(None)
        approvals = set()
        previous = None
        while asyncio.get_running_loop().time() < deadline:
            data = await self._json(session, "GET", f"/v1/runs/{run_id}", retries=2)
            if data.get("run_id", run_id) != run_id:
                raise HermesUnavailable()
            status = data.get("status")
            if status != previous and isinstance(status, str):
                previous = status
                emit({"event": "status", "status": status})
            if status in ("completed", "failed", "cancelled", "interrupted"):
                return self.final_text(data)
            if status == "waiting_for_approval":
                approval = data.get("approval")
                if not isinstance(approval, dict):
                    raise HermesUnavailable()
                request_id = self._identifier(approval.get("request_id"))
                if (
                    approval.get("event") != "approval.request"
                    or approval.get("run_id") != run_id
                    or not isinstance(approval.get("choices"), list)
                    or not {"once", "deny"}.issubset(approval["choices"])
                ):
                    raise HermesUnavailable()
                if request_id not in approvals:
                    approvals.add(request_id)
                    choice = "deny"
                    if approval_callback:
                        try:
                            choice = await asyncio.wait_for(
                                approval_callback(dict(approval)),
                                timeout=self.APPROVAL_TIMEOUT,
                            )
                        except Exception:
                            choice = "deny"
                    if choice not in ("once", "deny"):
                        choice = "deny"
                    await self._json(
                        session,
                        "POST",
                        f"/v1/runs/{run_id}/approval",
                        payload={"request_id": request_id, "choice": choice},
                    )
            elif status not in ("queued", "running", "stopping"):
                raise HermesUnavailable()
            await asyncio.sleep(self.POLL_INTERVAL)
        raise HermesUnavailable()

    async def recover_pending(self, on_terminal):
        """Deliver results of runs left pending by a previous process.

        Polls every persisted run to a terminal status, then awaits
        ``on_terminal(channel_id, text)``. A truthy return removes the record;
        a falsy return (or an exception) keeps it for the next startup. Pending
        runs are never re-admitted, so the work is not run twice.
        """
        if not self.key or not self.url:
            return
        records = self.list_pending()
        if not records:
            return
        now = time.time()
        async with aiohttp.ClientSession(
            headers=self._headers(), trust_env=False
        ) as session:
            for record in records:
                run_id = record["run_id"]
                if now - record["created_at"] > self.MAX_RECORD_AGE:
                    self.forget(run_id)
                    continue
                try:
                    deadline = asyncio.get_running_loop().time() + self.RUN_TIMEOUT
                    answer = await self._poll(session, run_id, None, deadline)
                except HermesUnavailable:
                    continue
                delivered = False
                try:
                    delivered = await on_terminal(record["channel_id"], answer)
                except Exception:
                    delivered = False
                if delivered:
                    self.forget(run_id)

    @staticmethod
    def final_text(data):
        if not isinstance(data, dict):
            raise HermesUnavailable()
        text = data.get("output")
        if not isinstance(text, str) or not text.strip():
            raise HermesUnavailable()
        complete = (
            data.get("status") == "completed"
            and data.get("completed", True) is True
            and not data.get("partial")
            and not data.get("interrupted")
        )
        if complete:
            return text.strip()
        if data.get("status") not in (
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        ):
            raise HermesUnavailable()
        return (
            "⚠️ A execução não terminou com sucesso. Resposta parcial:\n\n"
            + text.strip()
        )


def _safe_emitter(on_event):
    def emit(event):
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:
            # Progress display must never break or leak from the run itself.
            pass

    return emit


def _sse_event(frame):
    """Decode one SSE frame's JSON ``data:``; comments and malformed data are None."""
    lines = [
        line[5:].lstrip()
        for line in frame.decode("utf-8", "replace").split("\n")
        if line.startswith("data:")
    ]
    if not lines:
        return None
    try:
        event = json.loads("\n".join(lines))
    except ValueError:
        return None
    if not isinstance(event, dict) or not isinstance(event.get("event"), str):
        return None
    return event
