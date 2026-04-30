import logging
import os

import httpx
import streamlit as st
from dotenv import load_dotenv

from agents import run_agent
from tools import CONSENT_LABELS, Tools, registry

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ftw_app")

LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
LITELLM_MODEL     = os.getenv("LITELLM_MODEL",      "gpt-4o")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY",    "anything")
_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)

# ── Prompts ────────────────────────────────────────────────────────────────────

OPENING_PROMPT = """\
You are a warm, helpful assistant guiding an employee through a fitness-to-work
assessment. In a few short paragraphs:

1. Welcome them by name and explain that this assessment helps determine whether they
   are fit for work and what workplace support might be available to them.
2. Explain that their health data will only be accessed with their explicit consent —
   they are in control of what is shared.
3. Briefly describe the five categories of health data available (GP appointments,
   medical investigations, diagnoses, medications, and sick leave history) and why
   each is relevant to a fitness-to-work assessment.
4. Tell them they can grant consent for each category using the checkboxes on the left,
   and that they can share as many or as few as they choose.

Be warm and clear. Avoid medical jargon. Do not ask any questions — just explain.\
"""

DATA_PROMPT = """\
You are a fitness-to-work assessment assistant. You have been granted access to
retrieve a specific category of health records for the user.

Retrieve the requested data using the available tools, then present it clearly:
- Organise the information chronologically
- Highlight anything particularly relevant to fitness for work
- Keep a professional, non-judgemental tone
- Do not speculate beyond what the data shows\
"""

QA_PROMPT = """\
You are a fitness-to-work assessment assistant helping {person_name} understand
their health records.

The conversation history contains all the health data that has been retrieved so far.
Answer questions based on that information. Be clear, helpful, and non-judgemental.
If asked about a data category that has not been shared yet, suggest they tick the
relevant checkbox in the left sidebar.\
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def call_llm(messages: list[dict]) -> str:
    """Plain LLM call — no tools, returns text."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            f"{LITELLM_PROXY_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": LITELLM_MODEL, "messages": messages},
        )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def get_opening_message(person_info: dict) -> str:
    return call_llm([
        {"role": "system", "content": OPENING_PROMPT},
        {
            "role": "user",
            "content": (
                f"The user is {person_info['person_name']}, "
                f"{person_info['job_title']} in {person_info['department']}."
            ),
        },
    ])


def fetch_data(person_id: str, person_name: str, data_type: str, tools: Tools) -> str:
    label = CONSENT_LABELS[data_type]
    logger.info("Fetching %s for %s (%s)", label, person_name, person_id)
    return run_agent(
        messages=[
            {"role": "system", "content": DATA_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Retrieve ONLY the {label} for {person_name} "
                    f"(person_id: {person_id}). "
                    "Do not retrieve any other data categories."
                ),
            },
        ],
        tools=tools,
    )


def answer_question(question: str, person_name: str) -> str:
    context = [
        {"role": msg["role"], "content": msg["text"]}
        for msg in st.session_state.display_history
    ]
    return call_llm([
        {"role": "system", "content": QA_PROMPT.format(person_name=person_name)},
        *context,
        {"role": "user", "content": question},
    ])


def reset_for_person(person_id: str, person_name: str) -> None:
    st.session_state.person_id       = person_id
    st.session_state.person_name     = person_name
    st.session_state.display_history = []
    st.session_state.fetched_types   = set()
    st.session_state.reset_count     = st.session_state.get("reset_count", 0) + 1
    for dt in CONSENT_LABELS:
        try:
            registry.revoke(dt)
        except ValueError:
            pass


# ── Session state ──────────────────────────────────────────────────────────────

for _key, _default in [
    ("person_id",       None),
    ("person_name",     None),
    ("display_history", []),
    ("fetched_types",   set()),
    ("reset_count",     0),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Fitness to Work Assessment",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    "<style>[data-testid='stSidebar'] { min-width: 280px; max-width: 320px; }</style>",
    unsafe_allow_html=True,
)

tools = Tools()

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## Fitness to Work")

    people     = tools.list_people()["people"]
    name_to_id = {p["person_name"]: p["person_id"] for p in people}
    choice     = st.selectbox("Who are you?", ["— select —"] + list(name_to_id.keys()))

    if choice != "— select —":
        selected_id = name_to_id[choice]
        if st.session_state.person_id != selected_id:
            reset_for_person(selected_id, choice)
            st.rerun()

    if st.session_state.person_id:
        st.divider()
        st.markdown("**Share your health data**")
        st.caption("Tick a category to share it as part of this assessment.")

        for data_type, label in CONSENT_LABELS.items():
            checked = st.checkbox(
                label,
                key=f"cb_{data_type}_{st.session_state.reset_count}",
            )
            if checked:
                registry.grant(data_type)
            else:
                try:
                    registry.revoke(data_type)
                except ValueError:
                    pass

            if data_type in st.session_state.fetched_types:
                st.caption("✅ Retrieved")

# ── Main area ──────────────────────────────────────────────────────────────────

st.markdown(
    "<h2 style='margin-bottom:0'>Fitness to Work Assessment</h2>"
    "<p style='color:#505a5f;margin-top:4px'>Your health data, your choice</p>",
    unsafe_allow_html=True,
)
st.divider()

if not st.session_state.person_id:
    st.info("Select your name from the sidebar to begin.")
    st.stop()

# Opening message on first load
if not st.session_state.display_history:
    with st.spinner(""):
        try:
            text = get_opening_message(tools.get_person_info(st.session_state.person_id))
        except Exception as exc:
            st.error(f"Could not reach LLM proxy: {exc}")
            st.stop()
    st.session_state.display_history.append({"role": "assistant", "text": text})

# Fetch data for any newly consented types (checkbox just ticked)
newly_fetched = False
for data_type in CONSENT_LABELS:
    if registry.has_consent(data_type) and data_type not in st.session_state.fetched_types:
        with st.spinner(f"Retrieving {CONSENT_LABELS[data_type]}…"):
            try:
                text = fetch_data(
                    st.session_state.person_id,
                    st.session_state.person_name,
                    data_type,
                    tools,
                )
            except Exception as exc:
                text = f"Could not retrieve {CONSENT_LABELS[data_type]}: {exc}"
        st.session_state.display_history.append({"role": "assistant", "text": text})
        st.session_state.fetched_types.add(data_type)
        newly_fetched = True

if newly_fetched:
    st.rerun()

# Display conversation
for msg in st.session_state.display_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

# Chat Q&A
if user_text := st.chat_input("Ask a question about your records…"):
    with st.chat_message("user"):
        st.markdown(user_text)
    st.session_state.display_history.append({"role": "user", "text": user_text})

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("*Thinking…*")
        try:
            response = answer_question(user_text, st.session_state.person_name)
        except Exception as exc:
            response = f"Sorry, something went wrong: {exc}"
        placeholder.markdown(response)

    st.session_state.display_history.append({"role": "assistant", "text": response})
    st.rerun()
