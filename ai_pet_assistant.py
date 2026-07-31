"""AI Pet Care Assistant for PawPal+.

A single, self-contained module that turns a natural-language request from the
owner (e.g. "I only have 20 minutes before work, what should I do for my pets?")
into a prioritized, actionable care plan.

Design notes
------------
- It is grounded in the EXISTING PawPal+ data. The context handed to the model
  is built purely from ``Scheduler`` methods that already exist in
  ``pawpal_system`` (``pending_tasks``, ``upcoming_with_pets``,
  ``conflict_warning``) — this is not a generic chatbot.
- It makes ONE Google Gemini API call. No RAG, no tools, no multi-agent
  machinery — that would be overkill for this task and for a student project.
- Guardrails (empty input, no pets, no scheduled tasks, API/SDK errors) are
  handled deterministically *before* and *around* the model call, so a bad
  request can never crash the app.
- Basic logging records what happened (counts and outcomes) without ever
  logging the raw request text, the plan, or the API key.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from pawpal_system import Recurrence, Scheduler

# Module-level logger. The Streamlit app / CLI can configure handlers; if they
# don't, Python's "last resort" handler still surfaces warnings and errors.
logger = logging.getLogger("pawpal.ai")

# Default model — a fast, low-cost Gemini model well suited to a student demo.
# "gemini-flash-latest" is a stable alias that always tracks the current Flash
# model, so it won't break when a specific dated version is retired.
DEFAULT_MODEL = "gemini-flash-latest"

# Environment variable the Gemini SDK / this module reads the key from.
API_KEY_ENV = "GEMINI_API_KEY"

# How the assistant should behave. Sent as the model's system instruction; the
# volatile per-request data goes in the user message.
SYSTEM_PROMPT = """You are the AI Pet Care Assistant built into PawPal+, a pet \
care planning app. You help a busy pet owner decide what to do for their pets \
right now, using ONLY the pets and scheduled tasks provided to you.

Rules:
- Base every recommendation on the actual pets and tasks in the data. Never \
invent pets, tasks, or times that are not listed.
- Respect the owner's stated constraints (such as how many minutes they have). \
Add up task durations and recommend the set that fits, prioritizing URGENT and \
HIGH tasks and anything time-sensitive.
- If the owner mentions a pet that is not in the data, say so plainly and list \
the pets they actually have.
- If a scheduling conflict is noted in the data, mention it and suggest how to \
handle it.
- Match your answer to what the owner actually asked:
  - When they ask what to do, what to prioritize, or give a time constraint \
(e.g. "I have 20 minutes"), reply with a short, friendly, actionable PLAN: an \
ordered list of what to do (pet name, task, rough minutes, and time), a one-line \
reason for the ordering, and — if relevant — what to safely skip or defer.
  - For any other question (e.g. "when is Rex's dinner?", "does anything clash \
today?", "how long will grooming take?", "which tasks are urgent?"), answer \
DIRECTLY and concisely from the data. Do NOT force the ordered-plan format when \
it doesn't fit the question.
- Keep every answer concise, friendly, and grounded only in the data above."""


@dataclass
class CarePlanResult:
    """Outcome of an assistant request.

    ``status`` lets the UI choose how to present the message:
    - ``"ok"``            – a real AI-generated plan
    - ``"empty_input"``   – the owner submitted nothing
    - ``"no_pets"``       – there are no pets yet
    - ``"no_tasks"``      – there are pets but nothing pending
    - ``"unavailable"``   – the AI backend isn't set up (missing package/key)
    - ``"error"``         – the AI call failed or was blocked
    """

    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def build_task_context(scheduler: Scheduler) -> str:
    """Render the current PawPal+ state as plain text for the model.

    Uses only existing ``Scheduler`` data so the assistant is always grounded in
    the same schedule the rest of PawPal+ shows. Pure and side-effect free, so
    it can be unit-tested without any network access.
    """
    owner = scheduler.owner
    pets = scheduler.pets

    lines = [f"Owner: {owner.name}"]

    pet_summaries = [
        f"{pet.name} ({pet.type}, age {pet.age}, needs: {pet.care_needs or 'n/a'})"
        for pet in pets
    ]
    lines.append(f"Pets ({len(pets)}): " + ", ".join(pet_summaries))
    lines.append("")

    # upcoming_with_pets() returns pending tasks already ordered by time then
    # priority, each paired with its owning pet.
    upcoming = scheduler.upcoming_with_pets()
    lines.append(f"Pending tasks ({len(upcoming)}, soonest first):")
    for pet, task in upcoming:
        when = task.scheduled_time.strftime("%H:%M")
        row = (
            f"- {when} | {pet.name} | {task.title} "
            f"| priority={task.priority.value} | {task.duration} min"
        )
        if task.recurrence is not Recurrence.NONE:
            row += f" | repeats {task.recurrence.value}"
        lines.append(row)

    # Let the existing conflict detector speak for itself.
    warning = scheduler.conflict_warning()
    if warning:
        lines.append("")
        lines.append("Scheduler-detected conflicts:")
        lines.append(warning)

    return "\n".join(lines)


def _build_user_message(user_request: str, context: str) -> str:
    """Combine the grounded PawPal+ data with the owner's question."""
    return (
        "Here is the current PawPal+ data:\n\n"
        f"{context}\n\n"
        "The owner says:\n"
        f'"{user_request.strip()}"\n\n'
        "Give a prioritized, actionable plan based only on the data above."
    )


def generate_care_plan(
    user_request: str,
    scheduler: Scheduler,
    *,
    client=None,
    model: str = DEFAULT_MODEL,
) -> CarePlanResult:
    """Produce a care-plan recommendation for ``user_request``.

    ``client`` may be a Gemini client (or any object exposing the same
    ``models.generate_content`` interface) — this is used in tests to avoid real
    network calls. When ``None``, a real ``genai.Client()`` is created.
    """
    # --- Guardrail 1: empty / whitespace input -------------------------------
    if not user_request or not user_request.strip():
        logger.info("care_plan: rejected empty request")
        return CarePlanResult(
            "empty_input",
            "Tell me what you need — for example, "
            '"I have 20 minutes before work, what should I do for my pets?"',
        )

    # --- Guardrail 2: no pets ------------------------------------------------
    if not scheduler.pets:
        logger.info("care_plan: no pets in system")
        return CarePlanResult(
            "no_pets",
            "There are no pets in PawPal+ yet. Add a pet first and I can help "
            "you plan their care.",
        )

    # --- Guardrail 3: nothing scheduled --------------------------------------
    pending = scheduler.pending_tasks()
    if not pending:
        logger.info("care_plan: no pending tasks")
        return CarePlanResult(
            "no_tasks",
            "Nothing is scheduled right now, so there's nothing urgent to do. "
            "Add some tasks and I can help you prioritize them.",
        )

    context = build_task_context(scheduler)
    logger.info(
        "care_plan: request len=%d pets=%d pending_tasks=%d model=%s",
        len(user_request.strip()),
        len(scheduler.pets),
        len(pending),
        model,
    )

    # --- Create a client if one wasn't injected (real API path) --------------
    if client is None:
        try:
            from google import genai  # type: ignore
        except ImportError:
            logger.error("care_plan: google-genai SDK not installed")
            return CarePlanResult(
                "unavailable",
                "The AI assistant needs the 'google-genai' package. Install it "
                "with `pip install google-genai` and try again.",
            )

        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            logger.error("care_plan: %s not set", API_KEY_ENV)
            return CarePlanResult(
                "unavailable",
                f"The AI assistant isn't configured. Set your {API_KEY_ENV} "
                "environment variable (e.g. in a .env file) and try again.",
            )

        try:
            client = genai.Client(api_key=api_key)
        except Exception:  # pragma: no cover - construction rarely fails
            logger.exception("care_plan: failed to construct Gemini client")
            return CarePlanResult(
                "unavailable",
                "The AI assistant isn't configured correctly. Check your "
                f"{API_KEY_ENV} and try again.",
            )

    # --- The single AI call, fully guarded -----------------------------------
    try:
        response = client.models.generate_content(
            model=model,
            contents=_build_user_message(user_request, context),
            # Passed as a plain dict so no extra SDK types are needed here.
            config={
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens": 1600,
                "temperature": 0.7,
            },
        )
    except Exception as exc:  # any SDK/network/auth error must not crash the app
        # Log the exception type but keep the user-facing text generic and safe.
        logger.exception("care_plan: AI call failed (%s)", type(exc).__name__)
        return CarePlanResult(
            "error",
            "Sorry — I couldn't reach the AI assistant just now. Please check "
            "your connection or API key and try again.",
        )

    plan_text = _extract_text(response)
    if not plan_text:
        # Empty text usually means the response was blocked by a safety filter
        # or the prompt returned nothing usable.
        logger.warning("care_plan: empty or blocked response from model")
        return CarePlanResult(
            "error",
            "I wasn't able to produce a plan for that request. Try rephrasing "
            "it as a pet-care question.",
        )

    logger.info("care_plan: plan generated (%d chars)", len(plan_text))
    return CarePlanResult("ok", plan_text)


def _extract_text(response) -> str:
    """Pull the text out of a Gemini response, defensively.

    Accessing ``.text`` on a blocked response can raise, so this never lets an
    extraction problem escape — it just yields an empty string, which the caller
    treats as a failed plan.
    """
    try:
        return (response.text or "").strip()
    except Exception:  # blocked/empty candidates, unexpected shape, etc.
        return ""
