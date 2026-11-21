"""Adaptateur Google Gemini (API officielle, REST generateContent)."""

from __future__ import annotations

from .base import HttpLLMProvider


class GeminiProvider(HttpLLMProvider):
    name = "gemini"
    # Modèle Flash-Lite stable, gratuit dans l'offre gratuite d'après la page de tarifs.
    default_model = "gemini-3.1-flash-lite"
    default_env = "GEMINI_API_KEY"
    base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def _build_request(self, prompt: str, system: str | None) -> tuple[str, dict, dict]:
        url = f"{self.base_url}/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}  # en en-tête : jamais dans l'URL
        payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        return url, headers, payload

    def _extract_text(self, data: dict) -> str:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
