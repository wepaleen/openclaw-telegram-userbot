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

    # Regex for DeepSeek-style textual tool calls embedded in content:
    # <tool_call_begin>function<tool_sep>TOOL_NAME\n```json\n{ARGS}\n```<tool_call_end>
    _TEXTUAL_TOOL_CALL_RE = re.compile(
        r"<tool_call_begin>\s*function\s*<tool_sep>\s*"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
        r"```(?:json)?\s*(?P<args>\{.*?\})\s*```\s*"
        r"<tool_call_end>",
        re.DOTALL,
    )

    # Simpler variant: functionNAME\n{ARGS} (no XML tags)
    _SIMPLE_FUNC_CALL_RE = re.compile(
        r"function(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\n?"
        r"(?:```(?:json)?\s*)?(?P<args>\{.*?\})(?:\s*```)?"
        r"(?:\s*(?:<tool_call_end>)?)",
        re.DOTALL,
    )

    @classmethod
    def extract_tool_calls(cls, response: dict[str, Any]) -> list[OpenClawToolCall]:
        calls: list[OpenClawToolCall] = []
        choices = response.get("choices", [])
        if not choices:
            return calls

        message = choices[0].get("message", {})

        # 1) Standard OpenAI-format tool_calls
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

        if calls:
            return calls

        # 2) Parse textual tool calls from content (DeepSeek quirk)
        content = message.get("content") or ""
        if "<tool_call_begin>" in content or (content.strip().startswith("function") and "{" in content):
            textual = cls._parse_textual_tool_calls(content)
            if textual:
                log.warning(
                    "Parsed %d textual tool call(s) from content: %s",
                    len(textual),
                    [tc.name for tc in textual],
                )
                return textual

        return calls

    @classmethod
    def _parse_textual_tool_calls(cls, content: str) -> list[OpenClawToolCall]:
        """Parse tool calls embedded as text in the response content."""
        calls: list[OpenClawToolCall] = []

        # Try XML-tagged format first
        for i, match in enumerate(cls._TEXTUAL_TOOL_CALL_RE.finditer(content)):
            name = match.group("name").strip()
            try:
                args = json.loads(match.group("args"))
            except (json.JSONDecodeError, ValueError):
                args = {}
            calls.append(OpenClawToolCall(
                call_id=f"textual:{name}:{i}",
                name=name,
                arguments=args if isinstance(args, dict) else {},
            ))

        if calls:
            return calls

        # Fallback: simple functionNAME {ARGS} format
        for i, match in enumerate(cls._SIMPLE_FUNC_CALL_RE.finditer(content)):
            name = match.group("name").strip()
            try:
                args = json.loads(match.group("args"))
            except (json.JSONDecodeError, ValueError):
                args = {}
            calls.append(OpenClawToolCall(
                call_id=f"textual:{name}:{i}",
                name=name,
                arguments=args if isinstance(args, dict) else {},
            ))

        return calls

    @classmethod
    def extract_text(cls, response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            return ""

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            text = content.strip()

            # Strip textual tool-call blocks (DeepSeek XML-tagged and simple formats)
            cleaned = cls._TEXTUAL_TOOL_CALL_RE.sub("", text)
            cleaned = cls._SIMPLE_FUNC_CALL_RE.sub("", cleaned)
            # Also strip any leftover XML tags
            cleaned = re.sub(r"</?tool_call_(?:begin|end)>", "", cleaned)
            cleaned = re.sub(r"</?tool_sep>", "", cleaned)
            cleaned = cleaned.strip()

            if cleaned != text.strip():
                log.warning("Stripped textual tool-call artifacts from content")
                return cleaned  # may be empty — runtime handles that

            return text
        return ""

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
