"""Tests for the AI Pet Care Assistant.

These exercise the deterministic guardrails and the data-grounding logic
without touching the network — the API path is tested with a fake client that
mimics the Anthropic Messages response shape.
"""

from __future__ import annotations

from datetime import datetime

from ai_pet_assistant import build_task_context, generate_care_plan
from pawpal_system import Owner, Pet, PriorityLevel, Scheduler, Task


def make_scheduler(*pets: Pet) -> Scheduler:
    owner = Owner(name="Sam")
    for pet in pets:
        owner.add_pet(pet)
    return Scheduler(owner)


def pet_with_task() -> Pet:
    rex = Pet(name="Rex", type="dog", age=3, care_needs="daily walks")
    rex.add_task(
        Task(
            title="Morning walk",
            priority=PriorityLevel.HIGH,
            scheduled_time=datetime(2026, 6, 28, 8, 0),
            duration=30,
        )
    )
    return rex


# --- Fake Gemini client ----------------------------------------------------


class _Response:
    """Mimics a Gemini response object, which exposes ``.text``."""

    def __init__(self, text) -> None:
        self.text = text


class _Models:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeClient:
    """Stand-in for genai.Client with a canned response."""

    def __init__(self, text: str = "1. Rex: Morning walk (30 min)") -> None:
        self.models = _Models(_Response(text))


class BlockedClient:
    """Simulates a safety-blocked / empty response (no usable text)."""

    def __init__(self) -> None:
        self.models = _Models(_Response(None))


class ExplodingClient:
    class _M:
        def generate_content(self, **kwargs):
            raise RuntimeError("network down")

    def __init__(self) -> None:
        self.models = ExplodingClient._M()


# --- Guardrails ------------------------------------------------------------


def test_empty_input_is_handled() -> None:
    scheduler = make_scheduler(pet_with_task())
    result = generate_care_plan("   ", scheduler, client=FakeClient())
    assert result.status == "empty_input"
    assert not result.ok


def test_no_pets_is_handled() -> None:
    scheduler = make_scheduler()
    result = generate_care_plan("what now?", scheduler, client=FakeClient())
    assert result.status == "no_pets"


def test_no_pending_tasks_is_handled() -> None:
    pet = Pet(name="Mochi", type="cat", age=2, care_needs="indoor")
    scheduler = make_scheduler(pet)  # pet has no tasks
    result = generate_care_plan("what now?", scheduler, client=FakeClient())
    assert result.status == "no_tasks"


def test_api_error_is_caught_not_raised() -> None:
    scheduler = make_scheduler(pet_with_task())
    result = generate_care_plan("help", scheduler, client=ExplodingClient())
    assert result.status == "error"
    assert "couldn't reach" in result.message.lower()


def test_blocked_or_empty_response_is_handled() -> None:
    scheduler = make_scheduler(pet_with_task())
    result = generate_care_plan("help", scheduler, client=BlockedClient())
    assert result.status == "error"


# --- Grounding & happy path ------------------------------------------------


def test_context_is_built_from_real_data() -> None:
    scheduler = make_scheduler(pet_with_task())
    context = build_task_context(scheduler)
    assert "Rex" in context
    assert "Morning walk" in context
    assert "priority=high" in context


def test_successful_plan_returns_model_text() -> None:
    scheduler = make_scheduler(pet_with_task())
    client = FakeClient(text="Do the morning walk first.")
    result = generate_care_plan("20 minutes before work", scheduler, client=client)

    assert result.ok
    assert result.message == "Do the morning walk first."

    # The owner's real task data must actually be sent to the model.
    sent = client.models.calls[0]["contents"]
    assert "Morning walk" in sent
    assert "20 minutes before work" in sent
