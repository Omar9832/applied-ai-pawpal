"""Tests for the AI Pet Care Assistant.

These exercise the deterministic guardrails and the data-grounding logic
without touching the network — the API path is tested with a fake client that
mimics the Anthropic Messages response shape.
"""

from __future__ import annotations

from datetime import datetime
from ai_pet_assistant import (
    INITIAL_COOLDOWN_SECONDS,
    _is_care_request,
    build_task_context,
    generate_care_plan,
    next_cooldown_state,
)
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


def test_over_long_input_is_rejected() -> None:
    scheduler = make_scheduler(pet_with_task())
    client = FakeClient()
    result = generate_care_plan("x" * 1001, scheduler, client=client)
    assert result.status == "too_long"
    assert client.models.calls == []  # rejected before any API call


def test_medical_question_is_deflected() -> None:
    scheduler = make_scheduler(pet_with_task())
    client = FakeClient()
    result = generate_care_plan(
        "My dog is bleeding and won't eat!", scheduler, client=client
    )
    assert result.status == "medical"
    assert "vet" in result.message.lower()
    assert client.models.calls == []  # deflected before any API call


def test_cooldown_escalates_and_resets() -> None:
    # First request is always allowed and starts at the initial cooldown.
    allowed, _, cooldown, wait = next_cooldown_state(None, INITIAL_COOLDOWN_SECONDS, 100.0)
    assert allowed is True
    assert cooldown == INITIAL_COOLDOWN_SECONDS
    assert wait == 0.0

    # Sending again immediately -> blocked, cooldown grows, wait is reported.
    allowed, _, grown, wait = next_cooldown_state(100.0, INITIAL_COOLDOWN_SECONDS, 100.5)
    assert allowed is False
    assert grown > INITIAL_COOLDOWN_SECONDS  # escalated
    assert wait > 0

    # Waiting past the (grown) cooldown -> allowed again and reset to initial.
    allowed, _, reset, wait = next_cooldown_state(100.0, grown, 100.0 + grown + 1)
    assert allowed is True
    assert reset == INITIAL_COOLDOWN_SECONDS


def test_history_is_included_in_prompt() -> None:
    scheduler = make_scheduler(pet_with_task())
    client = FakeClient()
    history = [
        {"role": "user", "content": "Which tasks are urgent?"},
        {"role": "assistant", "content": "Rex's Morning walk is high priority."},
    ]
    generate_care_plan("why?", scheduler, history=history, client=client)
    sent = client.models.calls[0]["contents"]
    assert "Which tasks are urgent?" in sent  # prior turn carried into the prompt


def test_greeting_is_not_blocked_by_guardrails() -> None:
    # A greeting with no pets should still reach the model, not the canned
    # "add a pet first" message.
    scheduler = make_scheduler()  # no pets
    result = generate_care_plan("hey, how are you?", scheduler, client=FakeClient())
    assert result.status == "ok"


def test_care_intent_classification() -> None:
    scheduler = make_scheduler(pet_with_task())
    assert _is_care_request("what should I do for my pets?", scheduler) is True
    assert _is_care_request("I have 20 minutes before work", scheduler) is True
    assert _is_care_request("what does Rex need today?", scheduler) is True  # pet name
    assert _is_care_request("how are you doing?", scheduler) is False
    assert _is_care_request("hello there", scheduler) is False


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
