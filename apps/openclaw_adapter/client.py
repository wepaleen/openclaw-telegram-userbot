"""OpenClaw chat-completions client with tool-calling helpers."""

import json
import logging
import re
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
            text = content.strip()
            # Strip pseudo-tool-call artifacts that some models produce
            # e.g. "functionsend_message {\"text\":\"actual reply\",\"chat_query\":\"...\"}"
            # e.g. "functionweb_search {\"query\":\"...\",\"limit\":3}"
            if text.startswith("function") and "{" in text:
                brace_idx = text.index("{")
                try:
                    payload = json.loads(text[brace_idx:])
                    if isinstance(payload, dict):
                        # If there's a "text" field, use it as the reply
                        if payload.get("text"):
                            log.warning(
                                "Stripped pseudo-tool-call prefix from text response (had text field)"
                            )
                            return str(payload["text"])
                        # Otherwise, it's a tool call with no text —
                        # return a safe fallback instead of raw JSON
                        func_name = text[len("function"):brace_idx].strip()
                        log.warning(
                            "Stripped pseudo-tool-call '%s' with no text field — returning fallback",
                            func_name,
                        )
                        # Check if there's trailing text after the JSON block
                        after_json = text[brace_idx:]
                        # Find the end of JSON object (matching braces)
                        depth = 0
                        json_end = 0
                        for i, ch in enumerate(after_json):
                            if ch == "{":
                                depth += 1
                            elif ch == "}":
                                depth -= 1
                                if depth == 0:
                                    json_end = brace_idx + i + 1
                                    break
                        trailing = text[json_end:].strip() if json_end else ""
                        if trailing:
                            return trailing
                        return ""
                except (json.JSONDecodeError, ValueError):
                    pass
            return text
        return "Не смог получить текстовый ответ от OpenClaw."

    @staticmethod
    def _extract_textual_tool_call(content: Any) -> OpenClawToolCall | None:
        if not isinstance(content, str):
            return None

        raw = content.strip()
        if not raw or "{" not in raw or "}" not in raw:
            return None

        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw, count=1).strip()
            raw = re.sub(r"\s*```$", "", raw).strip()

        brace_index = raw.find("{")
        if brace_index <= 0:
            return None

        header = raw[:brace_index].strip().strip(":")
        payload = raw[brace_index:].strip()

        if header.lower().startswith("function"):
            header = header[len("function"):].strip()

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", header):
            return None

        try:
            arguments = json.loads(payload)
        except Exception:
            return None
        if not isinstance(arguments, dict):
            return None

        return OpenClawToolCall(
            call_id=f"textual:{header}",
            name=header,
            arguments=arguments,
        )
