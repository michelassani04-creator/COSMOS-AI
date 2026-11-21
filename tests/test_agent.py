import pytest

from cosmos_ai.agent import CosmosAgent
from cosmos_ai.demo import BuildDraft, PickTopic, WriteScript
from cosmos_ai.llm import FakeLLM
from cosmos_ai.memory import EditorialMemory
from cosmos_ai.models import Draft


class RecordingPublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.published: list[Draft] = []

    def publish(self, draft):
        if self.fail:
            raise RuntimeError("Facebook indisponible")
        self.published.append(draft)
        return f"fb-{draft.id}"


class RecordingNotifier:
    def __init__(self):
        self.requests: list[Draft] = []

    def request_approval(self, draft):
        self.requests.append(draft)


class BoomStep:
    name = "boom"

    def run(self, ctx):
        raise RuntimeError("explosion")


def make_agent(mode="manual", publisher=None, steps=None):
    memory = EditorialMemory()
    publisher = publisher or RecordingPublisher()
    notifier = RecordingNotifier()
    steps = steps or [PickTopic(), WriteScript(FakeLLM()), BuildDraft()]
    agent = CosmosAgent(steps, memory, publisher, notifier, mode=mode)
    return agent, memory, publisher, notifier


def test_manual_mode_waits_for_approval():
    agent, memory, publisher, notifier = make_agent("manual")
    result = agent.run_cycle()
    assert result.status == "pending_approval"
    assert publisher.published == []
    assert len(notifier.requests) == 1
    assert memory.get_draft(result.draft.id).status == "pending_approval"


def test_approve_publishes():
    agent, memory, publisher, _ = make_agent("manual")
    draft_id = agent.run_cycle().draft.id
    result = agent.approve(draft_id)
    assert result.status == "published"
    assert memory.get_draft(draft_id).post_id == f"fb-{draft_id}"
    assert len(publisher.published) == 1


def test_reject_never_publishes_and_frees_the_topic():
    agent, memory, publisher, _ = make_agent("manual")
    result = agent.run_cycle()
    agent.reject(result.draft.id)
    assert publisher.published == []
    assert not memory.is_duplicate(result.draft.topic)


def test_cannot_approve_twice():
    agent, *_ = make_agent("manual")
    draft_id = agent.run_cycle().draft.id
    agent.approve(draft_id)
    with pytest.raises(ValueError):
        agent.approve(draft_id)


def test_auto_mode_publishes_without_asking():
    agent, _, publisher, notifier = make_agent("auto")
    assert agent.run_cycle().status == "published"
    assert len(publisher.published) == 1
    assert notifier.requests == []


def test_publish_failure_is_recorded_and_retryable():
    publisher = RecordingPublisher(fail=True)
    agent, memory, _, _ = make_agent("auto", publisher=publisher)
    result = agent.run_cycle()
    assert result.status == "failed"
    assert memory.get_draft(result.draft.id).status == "publish_failed"

    publisher.fail = False
    assert agent.approve(result.draft.id).status == "published"


def test_second_cycle_picks_another_topic_then_skips():
    agent, *_ = make_agent("auto")
    first = agent.run_cycle()
    second = agent.run_cycle()
    third = agent.run_cycle()
    assert first.draft.topic != second.draft.topic
    assert third.status == "skipped"


def test_failing_step_does_not_crash_the_agent():
    agent, *_ = make_agent(steps=[BoomStep()])
    result = agent.run_cycle()
    assert result.status == "failed"
    assert "boom" in result.error


def test_invalid_mode_is_refused():
    with pytest.raises(Exception):
        make_agent("turbo")
