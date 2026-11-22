"""Services provisoires, en attendant les phases suivantes."""

from __future__ import annotations

from .errors import NotConnected
from .models import Draft


class NotConnectedPublisher:
    """Facebook n'est pas encore connecté (phase 5) : toute publication est refusée."""

    def publish(self, draft: Draft) -> str:
        raise NotConnected("Facebook n'est pas encore connecté (phase 5)")


class ConsoleNotifier:
    def request_approval(self, draft: Draft) -> None:
        print(f"[validation requise] Reel #{draft.id} : {draft.topic}")
