"""OpenClaw chat-completions client with tool-calling helpers."""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from config import settings

log = logging.getLogger("openclaw_adapter.client")


@dataclass(slots=True)
class OpenClawToolCall:
    """Normalized tool call extracted from an OpenClaw response."""

    call_id: str
    name: str
    arguments: dict[str, Any]


class OpenClawChatClient:
    """Thin wrapper around OpenClaw's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.base_url = base_url or settings.chat_completions_url
        self.token = token or settings.openclaw_token
        self.model = model or f"openclaw:{settings.openclaw_agent_id}"
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        session_key: str = "",
        tool_choice: str = "auto",
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if session_key:
            headers["x-openclaw-session-key"] = session_key

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        log.info(
            "OpenClaw adapter request — session=%s, messages=%d, tools=%d, tool_choice=%s",
            session_key,
            len(messages),
            len(tools or []),
            tool_choice,
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.base_url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def extract_tool_calls(response: dict[str, Any]) -> list[OpenClawToolCall]:
        calls: list[OpenClawToolCall] = []
        choices = response.get("choices", [])
        if not choices:
            return calls

        message = choices[0].get("message", {})
        for tool_call in message.get("tool_calls", []):
            function = tool_call.get("function", {})
            raw_args = function.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}

            calls.append(
                OpenClawToolCall(
                    call_id=str(tool_call.get("id")),
                    name=str(function.get("name")),
                    arguments=args,
                )
            )
        return calls

    @staticmethod
    def extract_text(response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            return "Не смог получить ответ от OpenClaw."

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        return "Не смог получить текстовый ответ от OpenClaw."
