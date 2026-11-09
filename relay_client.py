"""WebSocket client to the VPS relay for the Linux AI agent.

TLS is verified against the private CA (certs/ca.pem) produced by
tools/make_relay_ca.sh - the same CA the firmware pins - so the agent trusts
only our own relay, reached by IP. Auth is a Bearer token in the upgrade
request. Auto-reconnects with capped backoff.

Message correlation: call_device() sends a `device_call` with a fresh
request_id and awaits the matching `device_result`. Inbound `chat`/`cancel`
(relayed from the browser via the ESP32) are handed to callbacks.
"""
import asyncio
import base64
import json
import ssl
import uuid
from typing import Awaitable, Callable, Optional

import websockets


class RelayClient:
    def __init__(self, url: str, token: str, ca_file: str):
        self._url = url
        self._token = token
        self._ctx = ssl.create_default_context(cafile=ca_file)
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._frame_waiters: list[asyncio.Future] = []
        self.latest_frame: Optional[bytes] = None
        self.frame_sw = 0   # real screen width reported with the last frame
        self.frame_sh = 0
        self.on_chat: Optional[Callable[[dict], Awaitable[None]]] = None
        self.on_cancel: Optional[Callable[[dict], Awaitable[None]]] = None
        self.on_confirm: Optional[Callable[[dict], Awaitable[None]]] = None

    async def run_forever(self):
        """Connect and pump messages, reconnecting on drop (backoff 1..30s)."""
        backoff = 1
        while True:
            try:
                async with websockets.connect(
                    self._url,
                    ssl=self._ctx,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    # Must exceed the relay cap (256 KB) so a base64 screenshot
                    # frame is not rejected as "message too big" (which silently
                    # dropped the connection -> "relay disconnected").
                    max_size=512 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self._ws = ws
                    backoff = 1
                    await self._send({"type": "hello", "role": "agent"})
                    print(f"[relay] connected to {self._url}")
                    async for raw in ws:
                        await self._dispatch(raw)
            except Exception as e:  # noqa: BLE001 - keep the agent alive
                print(f"[relay] disconnected: {e}; retry in {backoff}s")
            finally:
                self._ws = None
                self._fail_pending("relay disconnected")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _dispatch(self, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = msg.get("type")
        if mtype == "device_result":
            fut = self._pending.pop(msg.get("request_id", ""), None)
            if fut and not fut.done():
                fut.set_result(msg)
        elif mtype == "chat" and self.on_chat:
            await self.on_chat(msg)
        elif mtype == "cancel" and self.on_cancel:
            await self.on_cancel(msg)
        elif mtype == "confirm" and self.on_confirm:
            await self.on_confirm(msg)
        elif mtype == "frame":
            img = None
            b64 = msg.get("image")
            if b64:
                try:
                    img = base64.b64decode(b64)
                except (ValueError, TypeError):
                    img = None
            self.latest_frame = img
            self.frame_sw = int(msg.get("sw") or 0)
            self.frame_sh = int(msg.get("sh") or 0)
            for fut in self._frame_waiters:
                if not fut.done():
                    fut.set_result(img)
            self._frame_waiters.clear()

    def _fail_pending(self, reason: str):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending.clear()
        for fut in self._frame_waiters:
            if not fut.done():
                fut.set_result(None)
        self._frame_waiters.clear()

    def arm_frame(self) -> asyncio.Future:
        """Register interest in the NEXT screenshot frame. Call before sending
        the screenshot device_call, then await the returned future (with your
        own timeout). Resolves to JPEG bytes, or None on failure/disconnect."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._frame_waiters.append(fut)
        return fut

    async def _send(self, msg: dict):
        if self._ws is None:
            raise RuntimeError("relay not connected")
        await self._ws.send(json.dumps(msg))

    async def send_ui(self, msg: dict):
        """Progress/assistant/done/error/confirm_request toward the browser."""
        try:
            await self._send(msg)
        except RuntimeError:
            pass  # UI updates are best-effort

    async def call_device(self, tool: str, arguments: dict, task_id: str,
                          timeout: float = 10.0) -> dict:
        """Send a device_call and await its device_result. Raises on timeout."""
        request_id = uuid.uuid4().hex[:16]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        await self._send({
            "type": "device_call",
            "task_id": task_id,
            "request_id": request_id,
            "tool": tool,
            "arguments": arguments,
        })
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise
