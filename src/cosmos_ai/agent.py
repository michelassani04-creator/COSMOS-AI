"""L'agent unique de COSMOS-AI.

Un cycle = exécuter les étapes dans l'ordre, puis passer par la « porte de
validation » : en mode manual le Reel attend l'humain, en mode auto il est publié.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from .errors import ConfigError
from .memory import EditorialMemory
from .models import Draft
from .pipeline import CycleContext, Step
from .ports import Notifier, Publisher

log = logging.getLogger(__name__)

PENDING = "pending_approval"
APPROVED = "approved"
PUBLISHED = "published"
PUBLISH_FAILED = "publish_failed"
REJECTED = "rejected"


@dataclass
class CycleResult:
    status: str  # published | pending_approval | skipped | failed
    draft: Draft | None = None
    error: str | None = None


class CosmosAgent:
    def __init__(
        self,
        steps: Sequence[Step],
        memory: EditorialMemory,
        publisher: Publisher,
        notifier: Notifier,
        mode: str = "manual",
    ) -> None:
        if mode not in ("manual", "auto"):
            raise ConfigError(f"mode invalide : {mode!r}")
        self.steps = list(steps)
        self.memory = memory
        self.publisher = publisher
        self.notifier = notifier
        self.mode = mode

    def run_cycle(self) -> CycleResult:
        ctx = CycleContext(memory=self.memory)
        for step in self.steps:
            try:
                step.run(ctx)
            except Exception as exc:  # une étape en échec ne doit pas tuer l'agent
                log.exception("étape %s en échec", step.name)
                return CycleResult("failed", error=f"{step.name}: {exc}")

        if ctx.draft is None:
            if ctx.topic is None:
                return CycleResult("skipped")
            return CycleResult("failed", error="aucun brouillon produit")

        if self.memory.is_duplicate(ctx.draft.topic):
            return CycleResult("skipped", error="sujet déjà traité")

        if self.mode == "auto":
            saved = self.memory.save_draft(ctx.draft, APPROVED)
            return self._publish(saved)

        saved = self.memory.save_draft(ctx.draft, PENDING)
        self.notifier.request_approval(saved)
        return CycleResult("pending_approval", draft=saved)

    def approve(self, draft_id: int) -> CycleResult:
        draft = self._get_waiting(draft_id)
        self.memory.set_status(draft_id, APPROVED)
        draft.status = APPROVED
        return self._publish(draft)

    def reject(self, draft_id: int) -> CycleResult:
        draft = self._get_waiting(draft_id)
        self.memory.set_status(draft_id, REJECTED)
        draft.status = REJECTED
        return CycleResult("rejected", draft=draft)

    def _get_waiting(self, draft_id: int) -> Draft:
        draft = self.memory.get_draft(draft_id)
        if draft is None:
            raise KeyError(f"brouillon introuvable : {draft_id}")
        if draft.status not in (PENDING, PUBLISH_FAILED):
            raise ValueError(f"brouillon {draft_id} non validable (statut : {draft.status})")
        return draft

    def _publish(self, draft: Draft) -> CycleResult:
        assert draft.id is not None
        try:
            post_id = self.publisher.publish(draft)
        except Exception as exc:
            log.exception("publication en échec")
            self.memory.set_status(draft.id, PUBLISH_FAILED)
            draft.status = PUBLISH_FAILED
            return CycleResult("failed", draft=draft, error=f"publication: {exc}")
        self.memory.set_status(draft.id, PUBLISHED, post_id=post_id)
        draft.status = PUBLISHED
        draft.post_id = post_id
        return CycleResult("published", draft=draft)
