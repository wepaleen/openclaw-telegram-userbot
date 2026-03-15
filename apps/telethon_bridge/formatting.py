"""Convert LLM markdown output to Telegram-compatible HTML."""

import re


def md_to_tg_html(text: str) -> str:
    """Convert common Markdown patterns to Telegram HTML.

    Supports: **bold**, *italic*, `code`, ```pre```, ~~strikethrough~~,
    [text](url), > blockquote.
    """
    # Code blocks (```...```) — must be first to avoid inner replacements
    text = re.sub(
        r"```(?:\w+)?\n(.*?)```",
        r"<pre>\1</pre>",
        text,
        flags=re.DOTALL,
    )

    # Inline code (`...`)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Bold (**...**)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)

    # Italic (*...*)  — avoid matching list items (line starts with "* ")
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", text)

    # Strikethrough (~~...~~)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Blockquotes (> ...) — Telegram doesn't support <blockquote> well,
    # convert to italic with bar
    text = re.sub(r"^>\s*(.+)$", r"┃ <i>\1</i>", text, flags=re.MULTILINE)

    # Heading markers (# ## ###) — just bold the text
    text = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Clean up leftover markdown list markers: "- " at line start → "• "
    text = re.sub(r"^- ", "• ", text, flags=re.MULTILINE)

    return text
