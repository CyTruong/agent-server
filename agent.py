"""Linux AI agent: drives the Momo device through the relay using Ollama.

Flow per user request (a `chat` message relayed from the browser via the ESP32):
  LLM picks a tool -> allowlist/arg check -> (confirm if risky) -> call_device ->
  feed the device_result/screenshot back to the LLM -> repeat, bounded by
  MAX_STEPS / TASK_TIMEOUT, cancellable from the UI.

Modules: config (env), sessions (one runner per device), runner (task loop), grounding (where to click), matching
and intents (pure heuristics), skill_select (prompt), llm (Ollama gateway),
relay_client (WebSocket), tools (schemas and risk rules). Settings: .env.example.
"""
import asyncio

from config import AGENT_TOKEN, CA_FILE, MODEL, OCR_ENABLE, RELAY_URL, UIA_ENABLE, \
    UIA_MARKS_ENABLE, UITARS_ENABLE, UITARS_MODEL
from relay_client import RelayClient
from sessions import SessionManager


async def main():
    if not RELAY_URL or not AGENT_TOKEN:
        raise SystemExit("Thiếu RELAY_URL hoặc AGENT_TOKEN trong .env")
    relay = RelayClient(RELAY_URL, AGENT_TOKEN, CA_FILE)
    SessionManager(relay)  # one TaskRunner per device; registers chat/cancel handlers
    print(f"[agent] model={MODEL} relay={RELAY_URL}")
    print(f"[agent] grounding: uia={UIA_ENABLE} uia_marks={UIA_MARKS_ENABLE} "
          f"ocr={OCR_ENABLE} ui-tars="
          + (f"{UITARS_MODEL}" if UITARS_ENABLE else "off"))
    await relay.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
