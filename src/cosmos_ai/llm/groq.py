"""Adaptateur Groq (API officielle, compatible OpenAI : chat/completions)."""

from __future__ import annotations

from .base import HttpLLMProvider


class GroqProvider(HttpLLMProvider):
    name = "groq"
    default_model = "llama-3.3-70b-versatile"  # modèle « production » de Groq
    default_env = "GROQ_API_KEY"
    url = "https://api.groq.com/openai/v1/chat/completions"

    def _build_request(self, prompt: str, system: str | None) -> tuple[str, dict, dict]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {"Authorization": f"Bearer {self._api_key}"}
        return self.url, headers, {"model": self.model, "messages": messages}

    def _extract_text(self, data: dict) -> str:
        return data["choices"][0]["message"]["content"]
