"""WebSocket client to the VPS relay for the Linux AI agent.

TLS is verified against the private CA (certs/ca.pem) produced by
tools/make_relay_ca.sh - the same CA the firmware pins - so the agent trusts
only our own relay, reached by IP. Auth is a Bearer token in the upgrade
request. Auto-reconnects with capped backoff.

Message correlation: call_device() sends a `device_call` with a fresh
request_id and awaits the matching `device_result`. Inbound `chat`/`cancel`
(relayed from the browser via the ESP32) are handed to callbacks.

Several devices can share the relay: the relay stamps each device message with
its `device_id`, and calls/frames here are kept apart by that id.
"""
import asyncio
import base64
import json
import ssl
import uuid
from typing import Awaitable, Callable, Optional

import websockets


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RelayClient:
    def __init__(self, url: str, token: str, ca_file: str):
        self._url = url
        self._token = token
        self._ctx = ssl.create_default_context(cafile=ca_file)
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        # (device_id, request_id, future) for each screenshot being waited on.
        self._frame_waiters: list[tuple[str, str, asyncio.Future]] = []
        # Real screen size reported with each device's last frame.
        self._frame_sizes: dict[str, tuple[int, int]] = {}
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
            # Firmware that tags frames lets us drop a late one from an older
            # request; an untagged frame goes to whoever waits on that device.
            device_id = msg.get("device_id") or ""
            mine = [w for w in self._frame_waiters if w[0] == device_id]
            rid = msg.get("request_id") or ""
            if rid and not any(r == rid for _, r, _ in mine):
                return
            img = None
            b64 = msg.get("image")
            if b64:
                try:
                    img = base64.b64decode(b64)
                except (ValueError, TypeError):
                    img = None
            self._frame_sizes[device_id] = (int(msg.get("sw") or 0), int(msg.get("sh") or 0))
            for _, _, fut in mine:
                if not fut.done():
                    fut.set_result(img)
            self._frame_waiters = [w for w in self._frame_waiters if w[0] != device_id]

    def frame_size(self, device_id: str = "") -> tuple[int, int]:
        """(width, height) of the device's real screen, (0, 0) if not known yet."""
        return self._frame_sizes.get(device_id, (0, 0))

    def _fail_pending(self, reason: str):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))
        self._pending.clear()
        self._release_frame_waiters()

    def _release_frame_waiters(self, device_id: str | None = None):
        """Resolve waiters with None: all of them, or one device's."""
        keep = []
        for waiter in self._frame_waiters:
            if device_id is None or waiter[0] == device_id:
                if not waiter[2].done():
                    waiter[2].set_result(None)
            else:
                keep.append(waiter)
        self._frame_waiters = keep

    def arm_frame(self, request_id: str = "", device_id: str = "") -> asyncio.Future:
        """Register interest in the NEXT screenshot frame. Call before sending
        the screenshot device_call, then await the returned future (with your
        own timeout). Resolves to JPEG bytes, or None on failure/disconnect. Only
        the newest request of a device wants a frame, so that device's earlier
        waiters are released."""
        self._release_frame_waiters(device_id)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._frame_waiters.append((device_id, request_id, fut))
        return fut

    async def _send(self, msg: dict):
        if self._ws is None:
            raise RuntimeError("relay not connected")
        await self._ws.send(json.dumps(msg))

    async def send_ui(self, msg: dict):
        """Progress/assistant/done/error/confirm_request toward the browser."""
        try:
            await self._send(msg)
        except Exception:  # noqa: BLE001 - UI updates are best-effort; the socket
            pass           # can also close mid-send (websockets.ConnectionClosed)

    async def call_device(self, tool: str, arguments: dict, task_id: str,
                          timeout: float = 10.0, request_id: str = "",
                          device_id: str = "") -> dict:
        """Send a device_call and await its device_result. Raises on timeout."""
        request_id = request_id or new_request_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            call = {
                "type": "device_call",
                "task_id": task_id,
                "request_id": request_id,
                "tool": tool,
                "arguments": arguments,
            }
            if device_id:
                call["device_id"] = device_id
            await self._send(call)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(request_id, None)
