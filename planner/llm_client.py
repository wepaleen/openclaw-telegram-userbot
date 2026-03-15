"""OpenClaw LLM client — text in, text out. No tool calling."""

import json
import logging

import httpx

from config import settings

log = logging.getLogger("llm")


async def call_llm(
    system: str,
    user: str,
    max_tokens: int = 1024,
    session_key: str = "",
) -> str:
    """Send a simple chat completion request to OpenClaw.
    Returns the model's text response.
    No tools, no function calling — just text.
    """
    headers = {
        "Authorization": f"Bearer {settings.openclaw_token}",
        "Content-Type": "application/json",
    }
    if session_key:
        headers["x-openclaw-session-key"] = session_key

    payload = {
        "model": f"openclaw:{settings.openclaw_agent_id}",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }

    url = settings.chat_completions_url
    log.info("LLM request — url=%s, system_len=%d, user_len=%d", url, len(system), len(user))

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    choices = data.get("choices", [])
    if not choices:
        log.warning("LLM response — no choices: %s", json.dumps(data)[:500])
        return ""

    content = choices[0].get("message", {}).get("content", "")
    log.info("LLM response — len=%d, preview=%s", len(content), content[:200])
    return content.strip()


def extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from LLM response text.
    Handles raw JSON, ```json blocks, and markdown formatting.
    """
    import re

    # Try raw JSON first
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Try ```json blocks
    match = re.search(r"```(?:json)?\s*\n?(.+?)\n?```", stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    match = re.search(r"\{.+\}", stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None
