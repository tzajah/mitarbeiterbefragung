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


def qr_payload(stack_id: str, page: int = 1) -> str:
    """Nur Stapel-ID + Seite (1 = Fragen, 2 = Freitext) – bewusst KEINE Bogen-Nummer:
    eine eindeutige Nummer pro Blatt wäre ein Re-Identifikationsrisiko."""
    return json.dumps({"m": "mbf", "v": 1, "s": stack_id, "p": page}, separators=(",", ":"))


def parse_payload(text: str) -> dict | None:
    try:
        d = json.loads(text)
        if d.get("m") == "mbf" and "s" in d:
            d["p"] = 2 if d.get("p") == 2 else 1  # fehlt "p" (Altbestand) → Seite 1
            return d
    except (ValueError, TypeError):
        pass
    return None
