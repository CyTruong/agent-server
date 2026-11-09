"""The single gateway to Ollama: every call gets a timeout, so a stuck model can
never hang a task."""
import asyncio
import os

import ollama

from config import LLM_TIMEOUT

client = ollama.AsyncClient(host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))


async def chat(*, timeout: float = LLM_TIMEOUT, **kwargs):
    """ollama chat bounded by `timeout`; raises asyncio.TimeoutError when exceeded."""
    return await asyncio.wait_for(client.chat(**kwargs), timeout=timeout)
