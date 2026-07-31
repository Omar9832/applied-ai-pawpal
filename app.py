from datetime import datetime

import streamlit as st

# Load a local .env (if present) so GEMINI_API_KEY reaches the Gemini SDK.
# Guarded so the app still runs if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from ai_pet_assistant import generate_care_plan
from pawpal_system import Owner, Pet, PriorityLevel, Recurrence, Scheduler, Task

# Map the UI's lowercase priority strings to the PriorityLevel enum.
PRIORITY_BY_LABEL = {level.value: level for level in PriorityLevel}
# Same for the recurrence dropdown.
RECURRENCE_BY_LABEL = {level.value: level for level in Recurrence}

st.set_page_config(
    page_title="PawPal+",
    page_icon="🐾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Modern Pet-Tech SaaS styling (injected CSS)
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');

:root {
    --bg: #F4F2FB;
    --card: #FFFFFF;
    --primary: #6D5AE6;
    --primary-2: #9B87FF;
    --accent: #EEEAFF;
    --text: #232338;
    --muted: #7A7A93;
    --green: #16A34A;
    --border: #ECEAF6;
}

html, body, [class*="css"], .stApp { font-family: 'Poppins', sans-serif; }
.stApp { background: var(--bg); }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2rem; padding-bottom: 6rem; }

h1, h2, h3, h4 { color: var(--text); font-weight: 600; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] {
    background: var(--card);
    border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }

.pp-logo {
    display: flex; align-items: center; gap: 10px;
    font-size: 1.5rem; font-weight: 700; color: var(--primary);
    margin: 0 0 1.4rem 4px;
}
.pp-logo .paw {
    background: var(--accent); border-radius: 14px;
    width: 44px; height: 44px; display: grid; place-items: center; font-size: 1.4rem;
}

/* Sidebar nav buttons: inactive = ghost, active = lavender pill */
[data-testid="stSidebar"] .stButton button {
    justify-content: flex-start; text-align: left;
    border: none; background: transparent; color: var(--muted);
    border-radius: 14px; font-weight: 500; padding: 0.6rem 0.9rem;
    box-shadow: none;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: #F5F3FF; color: var(--primary);
}
[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: var(--accent); color: var(--primary); font-weight: 600;
}

.pp-profile {
    display: flex; align-items: center; gap: 12px;
    padding: 12px; margin-top: 1rem;
    background: #F7F5FF; border-radius: 16px;
}
.pp-profile .avatar {
    width: 40px; height: 40px; border-radius: 50%;
    background: linear-gradient(135deg,var(--primary),var(--primary-2));
    display: grid; place-items: center; color: #fff; font-size: 1.1rem;
}
.pp-profile .name { font-weight: 600; color: var(--text); font-size: 0.92rem; }
.pp-profile .role { color: var(--muted); font-size: 0.78rem; }

/* ---------- Page header ---------- */
.pp-header h1 { margin: 0; font-size: 1.7rem; }
.pp-header p { margin: 2px 0 0; color: var(--muted); }

/* ---------- Cards (bordered containers) ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--card);
    border: 1px solid var(--border) !important;
    border-radius: 20px;
    padding: 6px 20px;
    box-shadow: 0 10px 30px rgba(80,60,170,0.05);
}

/* ---------- Stat cards ---------- */
.stat-grid { display: flex; gap: 16px; flex-wrap: wrap; margin: 4px 0 8px; }
.stat-card {
    flex: 1; min-width: 150px; background: var(--card);
    border: 1px solid var(--border); border-radius: 20px; padding: 18px 20px;
    box-shadow: 0 10px 30px rgba(80,60,170,0.05);
}
.stat-ico {
    width: 40px; height: 40px; border-radius: 12px; background: var(--accent);
    display: grid; place-items: center; font-size: 1.2rem; margin-bottom: 10px;
}
.stat-num { font-size: 1.8rem; font-weight: 700; color: var(--text); line-height: 1; }
.stat-label { color: var(--muted); font-size: 0.85rem; margin-top: 4px; }

/* ---------- Pet chips ---------- */
.pet-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 6px; }
.pet-card {
    background: var(--card); border: 1px solid var(--border); border-radius: 16px;
    padding: 14px 16px; min-width: 160px; box-shadow: 0 6px 18px rgba(80,60,170,0.05);
}
.pet-card .pet-name { font-weight: 600; color: var(--text); }
.pet-card .pet-meta { color: var(--muted); font-size: 0.8rem; margin-top: 2px; }
.pet-card .pet-species {
    display: inline-block; background: var(--accent); color: var(--primary);
    border-radius: 8px; padding: 1px 8px; font-size: 0.72rem; font-weight: 600; margin-top: 8px;
}

/* ---------- Inputs ---------- */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input,
[data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea {
    border-radius: 12px !important;
}

/* ---------- Primary buttons (main area) ---------- */
.stButton button[kind="primary"] {
    background: linear-gradient(135deg, var(--primary), var(--primary-2));
    color: #fff; border: none; border-radius: 12px; font-weight: 600;
    box-shadow: 0 8px 20px rgba(109,90,230,0.25);
}
.stButton button[kind="primary"]:hover { filter: brightness(1.05); }

/* ---------- Tables ---------- */
[data-testid="stTable"] table { border: none; }
[data-testid="stTable"] thead th {
    background: #F7F5FF; color: var(--primary); font-weight: 600; border: none;
}
[data-testid="stTable"] tbody td { border-color: var(--border); color: var(--text); }

/* ---------- Readability: keep body text dark on the light theme ---------- */
[data-testid="stChatMessageContent"],
[data-testid="stChatMessageContent"] p,
[data-testid="stChatMessageContent"] li { color: var(--text) !important; }
[data-testid="stWidgetLabel"] p, label p { color: var(--text) !important; }
[data-testid="stCaptionContainer"] p { color: var(--muted) !important; }

/* =========================================================
   Floating AI Assistant button + chat panel
   ========================================================= */
.st-key-ai_fab {
    position: fixed; bottom: 28px; right: 28px; z-index: 1001; width: 66px;
}
.st-key-ai_fab button {
    width: 66px !important; height: 66px !important; border-radius: 50% !important;
    font-size: 30px !important; padding: 0 !important;
    background: linear-gradient(135deg, var(--primary), var(--primary-2)) !important;
    color: #fff !important; border: none !important;
    box-shadow: 0 10px 26px rgba(109,90,230,0.5) !important;
    animation: aiPulse 2.6s ease-in-out infinite;
    transition: transform .22s ease, box-shadow .22s ease !important;
}
.st-key-ai_fab button:hover {
    transform: scale(1.14) rotate(8deg);
    box-shadow: 0 16px 38px rgba(155,135,255,0.75) !important;
    animation: none;
}
/* Hover tooltip */
.st-key-ai_fab::after {
    content: "Ask PawPal AI ✨";
    position: absolute; right: 80px; top: 50%; transform: translateY(-50%) translateX(8px);
    background: var(--text); color: #fff; padding: 8px 14px; border-radius: 12px;
    font-size: 0.82rem; white-space: nowrap; font-family: 'Poppins', sans-serif;
    opacity: 0; pointer-events: none; transition: opacity .2s ease, transform .2s ease;
    box-shadow: 0 8px 20px rgba(0,0,0,0.18);
}
.st-key-ai_fab:hover::after { opacity: 1; transform: translateY(-50%) translateX(0); }

@keyframes aiPulse {
    0%, 100% { box-shadow: 0 10px 26px rgba(109,90,230,0.5); }
    50% { box-shadow: 0 10px 40px rgba(155,135,255,0.85); }
}

/* Chat panel */
.st-key-ai_panel {
    position: fixed; bottom: 108px; right: 28px; z-index: 1000;
    width: 390px; max-height: 74vh; overflow-y: auto;
    background: var(--card); border: 1px solid var(--border) !important;
    border-radius: 22px; padding: 18px 18px 8px !important;
    box-shadow: 0 24px 60px rgba(60,40,140,0.28);
    animation: panelIn .22s ease;
}
@keyframes panelIn {
    from { opacity: 0; transform: translateY(14px) scale(0.98); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}
.st-key-ai_close button {
    background: #F3F1FC !important; color: var(--muted) !important; border: none !important;
    border-radius: 10px !important; font-weight: 700 !important; padding: 2px 10px !important;
    box-shadow: none !important;
}
.st-key-ai_close button:hover { background: #ECE8FB !important; color: var(--primary) !important; }

.ai-title { display: flex; align-items: center; gap: 8px; font-weight: 700; color: var(--text); font-size: 1.05rem; }
.ai-badge {
    width: 30px; height: 30px; border-radius: 10px; display: grid; place-items: center;
    background: linear-gradient(135deg, var(--primary), var(--primary-2)); color: #fff;
}
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "owner" not in st.session_state:
    st.session_state.owner = Owner(name="Jordan")
if "page" not in st.session_state:
    st.session_state.page = "Owner"
if "show_chat" not in st.session_state:
    st.session_state.show_chat = False
if "chat" not in st.session_state:
    st.session_state.chat = []  # list of {"role": ..., "content": ...}

owner: Owner = st.session_state.owner
scheduler = Scheduler(owner)

NAV = [
    ("Owner", "🏠"),
    ("Add a Pet", "🐾"),
    ("Schedule a Task", "📅"),
    ("Today's Schedule", "✅"),
    ("Browse & Filter", "🔍"),
]


def _go(page: str) -> None:
    st.session_state.page = page


def _toggle_chat() -> None:
    st.session_state.show_chat = not st.session_state.show_chat


def _close_chat() -> None:
    st.session_state.show_chat = False


def repeat_label(task: Task) -> str:
    """Human-friendly recurrence label for a table cell."""
    return "—" if task.recurrence is Recurrence.NONE else task.recurrence.value.title()


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="pp-logo"><span class="paw">🐾</span> PawPal+</div>',
        unsafe_allow_html=True,
    )
    for name, icon in NAV:
        st.button(
            f"{icon}  {name}",
            key=f"nav_{name}",
            on_click=_go,
            args=(name,),
            use_container_width=True,
            type="primary" if st.session_state.page == name else "secondary",
        )

    st.markdown(
        f"""
        <div class="pp-profile">
            <div class="avatar">{(owner.name[:1] or 'P').upper()}</div>
            <div>
                <div class="name">{owner.name or 'Pet Parent'}</div>
                <div class="role">Pet Parent</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="pp-header"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def render_owner() -> None:
    page_header("Dashboard", "Your pet-care command center")

    pets = owner.pets
    pending = scheduler.pending_tasks()
    conflicts = scheduler.conflicts()
    st.markdown(
        f"""
        <div class="stat-grid">
            <div class="stat-card"><div class="stat-ico">🐾</div>
                <div class="stat-num">{len(pets)}</div><div class="stat-label">Pets</div></div>
            <div class="stat-card"><div class="stat-ico">📋</div>
                <div class="stat-num">{len(pending)}</div><div class="stat-label">Pending tasks</div></div>
            <div class="stat-card"><div class="stat-ico">⚠️</div>
                <div class="stat-num">{len(conflicts)}</div><div class="stat-label">Conflicts</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.subheader("Owner")
        owner_name = st.text_input("Owner name", value=owner.name)
        owner.name = owner_name
        st.caption("Your name is remembered while you use the app.")

    if pets:
        chips = "".join(
            f'<div class="pet-card"><div class="pet-name">{p.name}</div>'
            f'<div class="pet-meta">age {p.age} · {p.care_needs or "no notes"}</div>'
            f'<div class="pet-species">{p.type}</div></div>'
            for p in pets
        )
        st.markdown(f'<div class="pet-grid">{chips}</div>', unsafe_allow_html=True)
    else:
        st.info("No pets yet. Head to **Add a Pet** to get started.")


def render_add_pet() -> None:
    page_header("Add a Pet", "Create a profile so PawPal+ can plan its care")

    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            pet_name = st.text_input("Pet name", value="Mochi")
            age = st.number_input("Age (years)", min_value=0, max_value=40, value=2)
        with c2:
            species = st.selectbox("Species", ["dog", "cat", "other"])
            care_needs = st.text_input("Care needs", value="", placeholder="e.g. daily walks")

        if st.button("Add pet", type="primary"):
            owner.add_pet(Pet(name=pet_name, type=species, age=int(age), care_needs=care_needs))
            st.success(f"Added {pet_name} ({species}).")

    if owner.pets:
        chips = "".join(
            f'<div class="pet-card"><div class="pet-name">{p.name}</div>'
            f'<div class="pet-meta">age {p.age} · {p.care_needs or "no notes"}</div>'
            f'<div class="pet-species">{p.type}</div></div>'
            for p in owner.pets
        )
        st.markdown(f'<div class="pet-grid">{chips}</div>', unsafe_allow_html=True)
    else:
        st.info("No pets yet. Add one above.")


def render_schedule_task() -> None:
    page_header("Schedule a Task", "Attach a care task to one of your pets")

    if not owner.pets:
        st.info("Add a pet first, then you can schedule tasks for it.")
        return

    with st.container(border=True):
        col1, col2 = st.columns(2)
        with col1:
            task_title = st.text_input("Task title", value="Morning walk")
            pet_choice = st.selectbox("For pet", [pet.name for pet in owner.pets])
            priority = st.selectbox("Priority", list(PRIORITY_BY_LABEL.keys()), index=2)
        with col2:
            duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
            task_time = st.time_input("Time")
            repeat = st.selectbox("Repeat", list(RECURRENCE_BY_LABEL.keys()))

        if st.button("Add task", type="primary"):
            pet = next(pet for pet in owner.pets if pet.name == pet_choice)
            scheduled = datetime.combine(datetime.now().date(), task_time)
            pet.add_task(
                Task(
                    title=task_title,
                    priority=PRIORITY_BY_LABEL[priority],
                    scheduled_time=scheduled,
                    duration=int(duration),
                    recurrence=RECURRENCE_BY_LABEL[repeat],
                )
            )
            st.success(f"Added '{task_title}' for {pet_choice}.")


def render_today() -> None:
    page_header("Today's Schedule", "Ordered by time, then priority — built by the Scheduler")

    warning = scheduler.conflict_warning()
    if warning:
        st.warning(f"⚠️ {warning}")

    upcoming = scheduler.upcoming_with_pets()
    if not upcoming:
        st.info("Nothing pending. Add some tasks to see your plan.")
        return

    with st.container(border=True):
        st.success(f"{len(upcoming)} task(s) planned for {owner.name}.")
        st.table(
            [
                {
                    "Time": task.scheduled_time.strftime("%H:%M"),
                    "Priority": task.priority.value.title(),
                    "Pet": pet.name,
                    "Task": task.title,
                    "Min": task.duration,
                    "Repeat": repeat_label(task),
                }
                for pet, task in upcoming
            ]
        )

        st.markdown("**Mark a task done**")
        task_by_label = {
            f"{task.scheduled_time.strftime('%H:%M')} · {pet.name}: {task.title}": task
            for pet, task in upcoming
        }
        done_col, btn_col = st.columns([4, 1])
        with done_col:
            choice = st.selectbox("Completed task", list(task_by_label.keys()))
        with btn_col:
            st.write("")
            mark_done = st.button("Done ✓", type="primary")

        if mark_done:
            follow_up = scheduler.complete_task(task_by_label[choice])
            if follow_up is not None:
                st.toast(
                    f"Rescheduled '{follow_up.title}' for "
                    f"{follow_up.scheduled_time.strftime('%a %H:%M')}."
                )
            st.rerun()


def render_browse() -> None:
    page_header("Browse & Filter Tasks", "Every task across all pets, narrowed to what you need")

    if not owner.pets:
        st.info("Add a pet and some tasks to browse them here.")
        return

    with st.container(border=True):
        fcol1, fcol2 = st.columns(2)
        with fcol1:
            pet_filter = st.selectbox("Pet", ["All pets"] + [pet.name for pet in owner.pets])
        with fcol2:
            status_filter = st.selectbox("Status", ["All", "Pending", "Completed"])

        completed_arg = {"All": None, "Pending": False, "Completed": True}[status_filter]
        pet_arg = None if pet_filter == "All pets" else pet_filter

        pet_by_id = {task.id: pet for pet in owner.pets for task in pet.tasks}
        filtered = scheduler.filter_tasks(completed=completed_arg, pet_name=pet_arg)
        filtered.sort(key=lambda task: task.scheduled_time)

        if not filtered:
            st.info("No tasks match these filters.")
        else:
            st.table(
                [
                    {
                        "Time": task.scheduled_time.strftime("%H:%M"),
                        "Priority": task.priority.value.title(),
                        "Pet": pet_by_id[task.id].name,
                        "Task": task.title,
                        "Min": task.duration,
                        "Repeat": repeat_label(task),
                        "Status": "✅ Done" if task.completed else "⏳ Pending",
                    }
                    for task in filtered
                ]
            )


PAGES = {
    "Owner": render_owner,
    "Add a Pet": render_add_pet,
    "Schedule a Task": render_schedule_task,
    "Today's Schedule": render_today,
    "Browse & Filter": render_browse,
}
PAGES.get(st.session_state.page, render_owner)()


# ---------------------------------------------------------------------------
# Floating AI Assistant (button + chat panel)
# ---------------------------------------------------------------------------
_STATUS_STYLE = {
    "ok": st.success,
    "empty_input": st.info,
    "no_pets": st.info,
    "no_tasks": st.info,
    "unavailable": st.warning,
    "error": st.error,
}

if st.session_state.show_chat:
    with st.container(key="ai_panel"):
        head_l, head_r = st.columns([5, 1])
        with head_l:
            st.markdown(
                '<div class="ai-title"><span class="ai-badge">🤖</span> PawPal AI Assistant</div>',
                unsafe_allow_html=True,
            )
        with head_r:
            st.button("✕", key="ai_close", on_click=_close_chat)

        st.caption("Grounded in your real pets & tasks — try *\"I have 20 minutes, what now?\"*")

        if not st.session_state.chat:
            st.chat_message("assistant").write(
                "Hi! Ask me what to prioritize for your pets and I'll build a quick plan."
            )
        for msg in st.session_state.chat:
            st.chat_message(msg["role"]).markdown(msg["content"])

        with st.form("ai_chat_form", clear_on_submit=True):
            prompt = st.text_area(
                "Message", label_visibility="collapsed",
                placeholder="Ask the assistant…", height=80,
            )
            send = st.form_submit_button("Send", type="primary", use_container_width=True)

        if send:
            st.session_state.chat.append({"role": "user", "content": prompt})
            with st.spinner("Thinking about your pets…"):
                result = generate_care_plan(prompt, scheduler)
            st.session_state.chat.append({"role": "assistant", "content": result.message})
            st.rerun()

# The floating button itself (toggles the panel).
st.button("🤖", key="ai_fab", on_click=_toggle_chat, help="Ask PawPal AI")
