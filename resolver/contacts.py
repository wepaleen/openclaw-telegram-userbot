"""Contact book: name -> username/user_id mapping with fuzzy search."""

import json
import logging
from typing import Any

from db import get_db

log = logging.getLogger("contacts")


def _contact_aliases(row: dict[str, Any]) -> list[str]:
    try:
        aliases = json.loads(row.get("aliases", "[]"))
    except json.JSONDecodeError:
        return []
    return [str(alias) for alias in aliases if str(alias).strip()]


async def add_contact(
    display_name: str,
    username: str | None = None,
    user_id: int | None = None,
    aliases: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    db = await get_db()
    await db.execute(
        "INSERT INTO contacts (display_name, username, user_id, aliases, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (display_name, username, user_id, json.dumps(aliases or []), notes),
    )
    await db.commit()
    return {"ok": True, "display_name": display_name, "username": username}


async def find_contact(query: str) -> dict[str, Any] | None:
    """Find a contact by name, username, or alias (case-insensitive)."""
    matches = await search_contacts(query)
    if len(matches) == 1:
        return matches[0]
    return None


async def search_contacts(query: str) -> list[dict[str, Any]]:
    """Search contacts and return all exact/partial matches."""
    db = await get_db()
    q = query.strip().lower()
    if not q:
        return []

    matches: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    def add_match(row: dict[str, Any]) -> None:
        contact_id = int(row["id"])
        if contact_id in seen_ids:
            return
        seen_ids.add(contact_id)
        matches.append(row)

    # Exact username match
    if q.startswith("@"):
        rows = await db.execute_fetchall(
            "SELECT * FROM contacts WHERE LOWER(username) = ?", (q,)
        )
        for row in rows:
            add_match(dict(row))
        if matches:
            return matches

    # Fetch all contacts once, compare in Python (SQLite LOWER() is ASCII-only)
    all_contacts = await db.execute_fetchall("SELECT * FROM contacts")

    # Exact display_name match
    for c in all_contacts:
        c_dict = dict(c)
        if (c_dict.get("display_name") or "").lower() == q:
            add_match(c_dict)
    if matches:
        return matches

    # Search in aliases (exact)
    for c in all_contacts:
        c_dict = dict(c)
        for alias in _contact_aliases(c_dict):
            if alias.lower() == q:
                add_match(c_dict)
                break
    if matches:
        return matches

    # Partial name/username/alias match
    for c in all_contacts:
        c_dict = dict(c)
        if q in (c_dict.get("display_name") or "").lower():
            add_match(c_dict)
            continue
        if c_dict.get("username") and q in str(c_dict["username"]).lower():
            add_match(c_dict)
            continue
        if any(q in alias.lower() for alias in _contact_aliases(c_dict)):
            add_match(c_dict)

    return matches


async def list_contacts(limit: int = 50) -> list[dict[str, Any]]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM contacts ORDER BY display_name LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]


async def get_contacts_summary() -> str:
    """Return contacts as JSON string for LLM prompts."""
    contacts = await list_contacts()
    summary = []
    for c in contacts:
        entry = {"name": c["display_name"]}
        if c.get("username"):
            entry["username"] = c["username"]
        if c.get("user_id"):
            entry["user_id"] = c["user_id"]
        aliases = json.loads(c.get("aliases", "[]"))
        if aliases:
            entry["aliases"] = aliases
        summary.append(entry)
    return json.dumps(summary, ensure_ascii=False)


async def resolve_name_to_target(name: str) -> str | int | None:
    """Resolve a human name to @username or user_id for Telegram API."""
    contact = await find_contact(name)
    if not contact:
        return None
    if contact.get("username"):
        return contact["username"]
    if contact.get("user_id"):
        return contact["user_id"]
    return None
