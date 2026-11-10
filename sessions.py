"""One TaskRunner per device, so several PCs can share one agent without mixing
their tasks, screenshots, pending questions, logs or conversation context.

The relay stamps every device message with the sender's `device_id` (taken from
its token, never from the payload); every message back carries the same id and
the relay delivers it only to that device.
"""
import asyncio

import agent_log
from agent_log import log
from config import UI_LOGS
from relay_client import RelayClient
from runner import TaskRunner


class DeviceChannel:
    """The relay as one device's session sees it: everything is addressed to it."""

    def __init__(self, relay: RelayClient, device_id: str):
        self._relay = relay
        self.device_id = device_id

    @property
    def frame_sw(self) -> int:
        return self._relay.frame_size(self.device_id)[0]

    @property
    def frame_sh(self) -> int:
        return self._relay.frame_size(self.device_id)[1]

    async def send_ui(self, msg: dict):
        if self.device_id:
            msg = {**msg, "device_id": self.device_id}
        await self._relay.send_ui(msg)

    async def call_device(self, tool: str, arguments: dict, task_id: str,
                          timeout: float = 10.0, request_id: str = "") -> dict:
        return await self._relay.call_device(tool, arguments, task_id, timeout=timeout,
                                             request_id=request_id, device_id=self.device_id)

    def arm_frame(self, request_id: str = "") -> asyncio.Future:
        return self._relay.arm_frame(request_id, device_id=self.device_id)


class SessionManager:
    def __init__(self, relay: RelayClient):
        self.relay = relay
        self.sessions: dict[str, TaskRunner] = {}
        relay.on_chat = self.on_chat
        relay.on_cancel = self.on_cancel
        relay.on_confirm = self.on_confirm
        if UI_LOGS:
            self._log_queue = agent_log.attach_queue()
            self._drain_task = asyncio.create_task(self._drain_logs())

    def session(self, device_id: str) -> TaskRunner:
        if device_id not in self.sessions:
            log(f"phiên mới cho thiết bị '{device_id or 'default'}'")
            self.sessions[device_id] = TaskRunner(DeviceChannel(self.relay, device_id))
        return self.sessions[device_id]

    @staticmethod
    def _device(msg: dict) -> str:
        return str(msg.get("device_id") or "")

    async def on_chat(self, msg: dict):
        await self.session(self._device(msg)).on_chat(msg)

    async def on_cancel(self, msg: dict):
        await self.session(self._device(msg)).on_cancel(msg)

    async def on_confirm(self, msg: dict):
        await self.session(self._device(msg)).on_confirm(msg)

    async def _drain_logs(self):
        """Mirror each task's log lines to the browser of the device it runs for.
        Lines logged outside any task are console-only. Best-effort."""
        while True:
            device_id, task_id, line = await self._log_queue.get()
            if device_id is None:
                continue
            msg = {"type": "log", "task_id": task_id, "text": line}
            if device_id:
                msg["device_id"] = device_id
            try:
                await self.relay.send_ui(msg)
            except Exception:  # noqa: BLE001
                pass
