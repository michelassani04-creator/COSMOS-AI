"""Démo sans réseau : montre l'agent, le routeur et la validation avec de faux services."""

from __future__ import annotations

from .agent import CosmosAgent, CycleResult
from .config import AppConfig
from .llm import FakeLLM, LLMRouter
from .memory import EditorialMemory
from .models import Draft
from .pipeline import CycleContext

DEMO_TOPICS = [
    "Pourquoi Vénus est-elle si chaude ?",
    "Que se passerait-il si on enlevait le CO2 de Vénus ?",
]


class PickTopic:
    name = "choix_du_sujet"

    def run(self, ctx: CycleContext) -> None:
        for topic in DEMO_TOPICS:
            if not ctx.memory.is_duplicate(topic):
                ctx.topic = topic
                return


class WriteScript:
    name = "scenario"

    def __init__(self, llm) -> None:
        self.llm = llm

    def run(self, ctx: CycleContext) -> None:
        if ctx.topic is None:
            return
        ctx.script = self.llm.generate(f"Écris un script de Reel de 45 s sur : {ctx.topic}")
        ctx.caption = ctx.topic


class BuildDraft:
    name = "brouillon"

    def run(self, ctx: CycleContext) -> None:
        if ctx.topic and ctx.script:
            ctx.draft = Draft(
                topic=ctx.topic,
                script=ctx.script,
                caption=ctx.caption or ctx.topic,
                video_path="(vidéo simulée)",
            )


class _DemoPublisher:
    def publish(self, draft: Draft) -> str:
        return f"demo-post-{draft.id}"


class _ConsoleNotifier:
    def request_approval(self, draft: Draft) -> None:
        print(f"[validation requise] Reel #{draft.id} : {draft.topic}")


def run_demo(config: AppConfig) -> CycleResult:
    memory = EditorialMemory(":memory:", threshold=config.similarity_threshold)
    router = LLMRouter([FakeLLM("fake")], cooldown_seconds=config.llm.cooldown_seconds)
    agent = CosmosAgent(
        steps=[PickTopic(), WriteScript(router), BuildDraft()],
        memory=memory,
        publisher=_DemoPublisher(),
        notifier=_ConsoleNotifier(),
        mode=config.mode,
    )
    return agent.run_cycle()
