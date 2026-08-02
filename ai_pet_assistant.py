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
# "gemini-flash-latest" tracks the current Flash model and is the one with free-
# tier availability on this project's key (~20 requests/day). Note: the free tier
# is small, so heavy use can hit a daily quota (handled gracefully below).
DEFAULT_MODEL = "gemini-flash-latest"

# Environment variable the Gemini SDK / this module reads the key from.
API_KEY_ENV = "GEMINI_API_KEY"

# Reject requests longer than this (guards against cost blow-ups and abuse).
MAX_REQUEST_CHARS = 1000

# Per-session escalating cooldown: a short initial gap between requests that
# grows the more the user spams, and resets once they wait it out.
INITIAL_COOLDOWN_SECONDS = 3.0
MAX_COOLDOWN_SECONDS = 30.0
COOLDOWN_GROWTH = 2.0

# Symptom / emergency terms that should trigger a "see a vet" disclaimer instead
# of a scheduling answer. Deliberately excludes "medication"/"medicine", which
# are legitimate scheduled care tasks the owner may ask about.
MEDICAL_KEYWORDS = (
    "emergency", "bleeding", "vomit", "throw up", "diarrhea", "seizure",
    "poison", "choking", "not breathing", "unconscious", "collaps",
    "not eating", "won't eat", "wont eat", "dying", "overdose", "in pain",
    "injured", "wound", "swollen", "sick", "hurt", "fever", "lethargic",
    "trembling", "limping",
)

# How the assistant should behave. Sent as the model's system instruction; the
# volatile per-request data goes in the user message.
SYSTEM_PROMPT = """You are the AI Pet Care Assistant built into PawPal+, a pet \
care planning app. You are friendly and concise, and you ground every answer in \
ONLY the pets and scheduled tasks you are given. Never invent pets, tasks, or \
times that are not listed.

Choose your reply style from what the owner actually said:
1. GREETING or SMALL TALK ("hi", "how are you?", "thanks"): reply warmly in one \
or two sentences. Do NOT list tasks or produce a plan.
2. OFF-TOPIC (not about their pets or schedule): give a brief, friendly reply, \
then gently remind them you're their pet-care assistant. Do NOT produce a plan.
3. DIRECT QUESTION about the data ("which tasks are urgent?", "when is Rex's \
dinner?", "how many pets do I have?"): answer directly and concisely from the \
data. Do NOT produce a full plan.
4. PLANNING REQUEST — what to do, what to prioritize, or a time constraint ("I \
have 20 minutes, what should I do?"): reply with a short PLAN — an ordered list \
(pet, task, minutes, time), a one-line reason for the ordering, and, if useful, \
what to safely defer. Respect stated time limits by adding up durations and \
favoring URGENT/HIGH tasks.

Also:
- If the owner mentions a pet that is not in the data, say so and list their \
actual pets.
- If there are no pets or no tasks yet, say so briefly and invite them to add \
some — but still answer greetings and small talk normally.
- If the data notes a scheduling conflict, mention it when it's relevant.
Keep every reply concise, friendly, and grounded only in the data above."""


@dataclass
class CarePlanResult:
    """Outcome of an assistant request.

    ``status`` lets the UI choose how to present the message:
    - ``"ok"``            – a real AI-generated plan
    - ``"empty_input"``   – the owner submitted nothing
    - ``"too_long"``      – the request exceeded ``MAX_REQUEST_CHARS``
    - ``"medical"``       – a health/emergency question was deflected to a vet
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


def _build_user_message(
    user_request: str, context: str, history: list | None = None
) -> str:
    """Combine the grounded PawPal+ data, recent chat, and the owner's message.

    ``history`` is an optional list of ``{"role", "content"}`` turns so the model
    can resolve follow-ups like "why?" — only the last few are included to keep
    the prompt small.
    """
    sections = [f"Here is the current PawPal+ data:\n\n{context}"]

    if history:
        lines = []
        for msg in history[-6:]:
            role = msg.get("role") if isinstance(msg, dict) else None
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            who = "Owner" if role == "user" else "Assistant"
            lines.append(f"{who}: {content}")
        sections.append("Recent conversation (for context):\n" + "\n".join(lines))

    sections.append(
        f'The owner now says:\n"{user_request.strip()}"\n\n'
        "Reply in the most appropriate style, using only the data above."
    )
    return "\n\n".join(sections)


# Keywords that signal the owner is actually asking about pet care / scheduling,
# rather than making small talk or asking something unrelated. Used only to
# decide whether the "no pets" / "no tasks" shortcuts should fire — a deliberately
# conservative heuristic so basic conversation is never blocked.
_CARE_KEYWORDS = (
    "pet", "dog", "cat", "puppy", "kitten", "walk", "feed", "food", "task",
    "schedule", "plan", "priorit", "urgent", "care", "groom", "litter", "vet",
    "medic", "dinner", "conflict", "overlap", "double-book", "due", "help",
    "what should i do", "what do i do", "what now", "to do", "minute",
)


def _is_care_request(user_request: str, scheduler: Scheduler) -> bool:
    """Rough check: is the owner asking about their pets or schedule?

    Greetings and unrelated questions return ``False`` so they aren't answered
    with a canned "add a pet first" message.
    """
    text = user_request.casefold()
    if any(word in text for word in _CARE_KEYWORDS):
        return True
    return any(pet.name and pet.name.casefold() in text for pet in scheduler.pets)


def _is_medical_or_emergency(user_request: str) -> bool:
    """True if the request looks like a health/emergency question for a vet."""
    text = user_request.casefold()
    return any(word in text for word in MEDICAL_KEYWORDS)


def next_cooldown_state(
    last_time: float | None,
    cooldown: float,
    now: float,
) -> tuple[bool, float, float, float]:
    """Escalating per-session cooldown between AI requests.

    Given the previous request's time (``last_time``, ``None`` if this is the
    first), the current required gap (``cooldown``), and the current time
    (``now``), return ``(allowed, new_last_time, new_cooldown, wait_seconds)``:

    - If enough time has passed (or it's the first request), the request is
      allowed and the cooldown resets to ``INITIAL_COOLDOWN_SECONDS``.
    - Otherwise it's blocked, the cooldown grows by ``COOLDOWN_GROWTH`` (capped at
      ``MAX_COOLDOWN_SECONDS``), and ``wait_seconds`` says how long to wait.

    Pure and time-injected so it is easy to unit-test.
    """
    if last_time is None or now - last_time >= cooldown:
        return True, now, INITIAL_COOLDOWN_SECONDS, 0.0
    new_cooldown = min(cooldown * COOLDOWN_GROWTH, MAX_COOLDOWN_SECONDS)
    wait = new_cooldown - (now - last_time)
    return False, now, new_cooldown, wait


def generate_care_plan(
    user_request: str,
    scheduler: Scheduler,
    *,
    history: list | None = None,
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

    text = user_request.strip()

    # --- Guardrail 2: request too long (cost / abuse guard) ------------------
    if len(text) > MAX_REQUEST_CHARS:
        logger.info("care_plan: rejected over-long request (%d chars)", len(text))
        return CarePlanResult(
            "too_long",
            f"That message is a bit long for me — please keep it under "
            f"{MAX_REQUEST_CHARS} characters and try again.",
        )

    # --- Guardrail 3: medical / emergency questions -------------------------
    # Safety: this is a scheduling assistant, not a vet. Deflect health and
    # emergency questions to a professional rather than answering them.
    if _is_medical_or_emergency(text):
        logger.warning("care_plan: medical/emergency request deflected")
        return CarePlanResult(
            "medical",
            "⚠️ I'm a pet-care *scheduling* assistant, not a vet — I can't help "
            "with health problems or emergencies. If your pet may be sick, hurt, "
            "or in danger, please contact your veterinarian right away, or an "
            "emergency animal hospital / pet poison helpline.",
        )

    # Only apply the "no pets" / "no tasks" shortcuts when the owner is actually
    # asking for care help — greetings and basic questions still reach the model.
    care_request = _is_care_request(user_request, scheduler)
    pending = scheduler.pending_tasks()

    # --- Guardrail 2: no pets ------------------------------------------------
    if care_request and not scheduler.pets:
        logger.info("care_plan: no pets in system")
        return CarePlanResult(
            "no_pets",
            "There are no pets in PawPal+ yet. Add a pet first and I can help "
            "you plan their care.",
        )

    # --- Guardrail 3: nothing scheduled --------------------------------------
    if care_request and not pending:
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
            contents=_build_user_message(user_request, context, history),
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
        detail = str(exc)
        if "RESOURCE_EXHAUSTED" in detail or "429" in detail:
            # Free-tier daily/minute quota reached — tell the user plainly.
            return CarePlanResult(
                "error",
                "The AI assistant has reached its free usage limit for now. "
                "Please wait a little and try again — the free quota resets over "
                "time (or add billing to your Gemini API key for higher limits).",
            )
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
