"""OpenClaw chat-completions client with tool-calling helpers."""

import json
import logging
from collections.abc import AsyncIterator
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
        self.token = token or settings.llm_api_key
        self.model = model or settings.llm_model
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
            data = response.json()

            # Log response details for debugging
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                finish = choices[0].get("finish_reason")
                tool_calls = msg.get("tool_calls", [])
                content_preview = (msg.get("content") or "")[:200]
                if tool_calls:
                    names = [tc.get("function", {}).get("name") for tc in tool_calls]
                    log.info(
                        "OpenClaw response — finish=%s, tool_calls=%s",
                        finish,
                        names,
                    )
                else:
                    log.info(
                        "OpenClaw response — finish=%s, NO tool_calls, content=%s",
                        finish,
                        content_preview,
                    )
            return data

    async def stream_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        session_key: str = "",
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Streaming chat completion — yields text chunks as they arrive.

        Only for conversational (no tools) requests.
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        log.info(
            "OpenClaw stream request — session=%s, messages=%d",
            session_key, len(messages),
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST", self.base_url, headers=headers, json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        text = delta.get("content")
                        if text:
                            yield text
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue

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
