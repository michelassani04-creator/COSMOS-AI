"""Extraction tolérante d'un objet JSON dans la réponse d'une IA."""

from __future__ import annotations

import json


def extract_json(text: str) -> dict:
    """Trouve le premier objet JSON de `text` (même entouré de texte ou de ```json)."""
    decoder = json.JSONDecoder()
    position = text.find("{")
    attempts = 0
    while position != -1 and attempts < 5:
        try:
            value, _ = decoder.raw_decode(text[position:])
        except ValueError:
            position = text.find("{", position + 1)
            attempts += 1
            continue
        if isinstance(value, dict):
            return value
        position = text.find("{", position + 1)
        attempts += 1
    raise ValueError("aucun objet JSON exploitable dans la réponse")
