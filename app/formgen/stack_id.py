"""QR-Code-Payload für Bogen-Stapel.

Ein Stapel = eine physische Abteilungsauslage. Der QR-Code trägt nur die
Stapel-ID (zufällige UUID) – der Stapel wird im System erst NACHdruck
einer Abteilung zugeordnet, niemals dem einzelnen Bogen.
"""
from __future__ import annotations

import json
import uuid


def new_stack_id() -> str:
    return uuid.uuid4().hex


def qr_payload(stack_id: str) -> str:
    return json.dumps({"m": "mbf", "v": 1, "s": stack_id}, separators=(",", ":"))


def parse_payload(text: str) -> dict | None:
    try:
        d = json.loads(text)
        if d.get("m") == "mbf" and "s" in d:
            return d
    except (ValueError, TypeError):
        pass
    return None
