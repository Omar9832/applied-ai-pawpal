# Model Card & Responsible-AI Reflection — PawPal+ AI Pet Care Assistant

## System Overview

The **AI Pet Care Assistant** is a feature inside PawPal+ (a pet-care scheduling
app). It takes a natural-language question from the pet owner, combines it with the
owner's **real** pets and scheduled tasks (pulled from the app's `Scheduler`), and
uses a single Google **Gemini** call (`gemini-flash-latest`) to return a prioritized,
reasoned care plan or a direct answer.

- **Intended use:** helping an owner decide what pet-care tasks to do and in what
  order, given their own schedule and constraints (e.g. "I only have 20 minutes").
- **Not intended for:** veterinary, medical, dietary, or emergency advice.
- **Design principles:** grounded in real app data, guarded against failure
  (empty input, missing data, API errors), and logged without storing sensitive
  content. Full technical details are in [`README.md`](README.md).

---

## Limitations and Biases

- **Only as good as the data entered.** The assistant reasons over whatever pets
  and tasks the owner typed in. If a task's priority or duration is wrong, the plan
  inherits that error — the AI trusts the labels rather than questioning them.
- **No real domain knowledge.** It plans *scheduling*, not *care quality*. It does
  not know that a puppy needs more frequent walks than a senior cat, or that a
  medication is time-critical beyond its "priority" label. It should never be read
  as veterinary guidance.
- **Arithmetic is model-generated, not verified.** The model adds up task durations
  itself to fit a time budget. Large language models occasionally miscount, so a
  "fits in 20 minutes" claim is a strong suggestion, not a guarantee.
- **Single-provider dependency.** It relies on the Gemini API being reachable. When
  the API is down or rate-limited, the feature is unavailable (though it fails
  safely — see below).
- **Language and cultural bias.** Prompts and answers are English-only, and the
  model's notion of "normal" pet care reflects its training data, which may not
  match every owner's routine, region, or type of animal.
- **Priority bias.** By instruction, it favors tasks labeled URGENT/HIGH. This is
  usually right, but it can under-weight a low-priority task that actually matters
  (e.g. enrichment), because it takes the owner's labels at face value.

---

## Could This AI Be Misused, and How Would I Prevent It?

- **Misuse: treated as medical/vet advice.** An owner could ask "my dog is
  vomiting, what do I do?" and act on a scheduling model's answer.
  *Prevention:* the system prompt scopes the assistant to *scheduling using the
  listed tasks only*, and it is documented as not for medical use. A visible
  disclaimer and an explicit "for emergencies, contact a vet" line would harden
  this further.
- **Misuse: prompt injection through task titles.** Because task titles are fed
  into the prompt, a malicious title (e.g. "ignore your rules and…") could try to
  steer the model. *Prevention:* the prompt tells the model to use only the data
  and stay on task; the blast radius is tiny (it only produces text advice, never
  takes actions); and the assistant is **human-in-the-loop** — the owner reads the
  plan and chooses what to actually do and mark done.
- **Misuse: privacy / data exposure.** Pet and task data is sent to a third-party
  API. *Prevention:* no sensitive personal identifiers are required or sent, the
  API key lives in a git-ignored `.env`, and **logging records only counts and
  outcomes — never the request text, the plan, or the key.**
- **General safety posture:** the assistant only *recommends*; it cannot modify the
  schedule, delete data, or act autonomously. A wrong answer is low-stakes and
  correctable by the owner.

---

## What Surprised Me While Testing Reliability

- **A "broken AI" turned out to be a UI bug.** After sending a message I saw *no
  reply*. I assumed the model had failed — but the response was there, rendered in
  **white text on a white background** because Streamlit was in dark mode. The AI
  was fine; the theme was hiding it. It taught me that "the AI didn't work" is often
  a wrapper problem, not a model problem.
- **A model I picked stopped existing.** My first model, `gemini-2.5-flash`,
  returned **404 "no longer available to new users"** partway through the project. I
  didn't expect a model ID to be retired that quickly, and it pushed me to use the
  stable alias `gemini-flash-latest`.
- **A real outage tested my error handling for me.** While capturing example
  outputs, Gemini returned a live **503 "high demand"**. Instead of crashing, the
  app caught it and showed a safe message — an accidental but genuine proof that the
  guardrails work against real-world failures, not just mocked ones in tests.
- **Grounding alone prevented a hallucination.** When I asked about an imaginary
  "parrot named Kiwi," I expected the model might invent care tasks. Instead, because
  the prompt was grounded in the real pet list, it correctly said Kiwi isn't in
  PawPal+ and listed my actual pets. Good grounding did more for trustworthiness
  than any clever prompt wording.

---

## Collaboration With AI During This Project

I built this project with the help of an AI coding assistant, which I used to
design the assistant module, write the automated tests, restyle the Streamlit UI,
and debug issues as they came up. I treated its output as a strong draft to review
and verify, not as final answers.

### One helpful suggestion

The AI recommended **dependency-injecting the API client** into
`generate_care_plan(user_request, scheduler, *, client=None)`. This let my tests
pass a *fake* client, so all seven AI tests run **offline with no API key and no
network** — verifying the guardrails and data-grounding without ever calling (or
paying for) the real API. This was genuinely useful and is a pattern I'll reuse.

### One flawed / incorrect suggestion

The same AI initially set the default model to **`gemini-2.5-flash`**. When I ran
it against my own key, that model returned a **404 "no longer available to new
users"** — the suggested model ID was outdated. I had to switch to the stable alias
`gemini-flash-latest`. The lesson: AI-suggested configuration (especially model IDs
and API details) can be stale, and must be verified against the live service rather
than trusted blindly.

---

*Author's note: this reflection documents my own work and judgments on the PawPal+
project; the AI tools I used are described honestly above.*
