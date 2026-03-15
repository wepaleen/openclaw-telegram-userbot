"""Google Sheets client using gspread + service account credentials."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from config import settings

log = logging.getLogger("google_sheets.client")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_gc: gspread.Client | None = None


def _get_client() -> gspread.Client:
    """Lazy-init gspread client from service account JSON file."""
    global _gc
    if _gc is not None:
        return _gc

    creds_path = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        os.path.join(os.path.dirname(settings.db_path) or ".", "service_account.json"),
    )
    if not os.path.isfile(creds_path):
        raise FileNotFoundError(
            f"Google service account key not found at {creds_path}. "
            "Set GOOGLE_SERVICE_ACCOUNT_JSON env var or place service_account.json in project root."
        )

    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    _gc = gspread.authorize(creds)
    log.info("Google Sheets client initialized from %s", creds_path)
    return _gc


def _extract_spreadsheet_id(url_or_id: str) -> str:
    """Extract spreadsheet ID from a full URL or return as-is if already an ID."""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    # Assume raw ID
    return url_or_id.strip()


def read_spreadsheet(
    spreadsheet: str,
    *,
    sheet_name: str | None = None,
    range_a1: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Read data from a Google Spreadsheet.

    Args:
        spreadsheet: URL or spreadsheet ID.
        sheet_name: Specific sheet/tab name. If None, uses first sheet.
        range_a1: A1 range notation like "A1:D10". If None, reads all values.
        limit: Max rows to return.

    Returns:
        Dict with sheet metadata and rows.
    """
    gc = _get_client()
    sheet_id = _extract_spreadsheet_id(spreadsheet)

    try:
        sp = gc.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        return {"error": f"Таблица не найдена. Убедитесь, что она расшарена на сервисный аккаунт."}
    except gspread.exceptions.APIError as e:
        return {"error": f"Google API error: {e}"}

    if sheet_name:
        try:
            ws = sp.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            available = [s.title for s in sp.worksheets()]
            return {
                "error": f"Лист '{sheet_name}' не найден.",
                "available_sheets": available,
            }
    else:
        ws = sp.sheet1

    if range_a1:
        values = ws.get(range_a1)
    else:
        values = ws.get_all_values()

    # Treat first row as headers if present
    headers: list[str] = []
    data_rows: list[list[str]] = []
    if values:
        headers = values[0]
        data_rows = values[1 : limit + 1]

    rows_as_dicts = []
    for row in data_rows:
        row_dict = {}
        for i, header in enumerate(headers):
            row_dict[header] = row[i] if i < len(row) else ""
        rows_as_dicts.append(row_dict)

    return {
        "spreadsheet_title": sp.title,
        "sheet": ws.title,
        "headers": headers,
        "rows": rows_as_dicts,
        "total_rows": len(data_rows),
        "range": range_a1 or "all",
    }


def list_sheets(spreadsheet: str) -> dict[str, Any]:
    """List all sheets/tabs in a spreadsheet."""
    gc = _get_client()
    sheet_id = _extract_spreadsheet_id(spreadsheet)

    try:
        sp = gc.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        return {"error": "Таблица не найдена. Убедитесь, что она расшарена на сервисный аккаунт."}

    sheets = []
    for ws in sp.worksheets():
        sheets.append({
            "title": ws.title,
            "id": ws.id,
            "row_count": ws.row_count,
            "col_count": ws.col_count,
        })

    return {
        "spreadsheet_title": sp.title,
        "sheets": sheets,
    }
