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
3. Tell them you will step through each category of health data one at a time,
   explaining what it is and why it is relevant, so they can decide what to share.

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
If asked about a category that has not been shared, suggest they tick it in the sidebar.\
"""

# ── Category explanations ──────────────────────────────────────────────────────
# Presented one at a time in the chat, each followed by Yes / No buttons.

CATEGORY_EXPLANATIONS: dict[str, str] = {
    "gp_appointment": (
        "**GP Appointment History**\n\n"
        "This shows when you have visited your GP and the reasons for those visits. "
        "It helps us understand any ongoing health needs and whether changes to your "
        "working arrangements might support you better.\n\n"
        "Would you like to share your GP appointment history?"
    ),
    "investigation": (
        "**Medical Investigations and Test Results**\n\n"
        "This covers tests or scans you have had — for example blood tests, X-rays, "
        "or MRI scans — and their results. Sharing this gives a clearer picture of any "
        "conditions that may be relevant to your fitness for work.\n\n"
        "Would you like to share your medical investigation results?"
    ),
    "diagnosis": (
        "**Diagnoses and Medical Conditions**\n\n"
        "This is a record of any conditions you have been diagnosed with and their "
        "current status. Understanding your diagnoses helps identify what workplace "
        "support or adjustments might be most useful for you.\n\n"
        "Would you like to share your diagnoses?"
    ),
    "medication": (
        "**Current and Past Medications**\n\n"
        "Your medication record shows what treatments you are on and what they are for. "
        "Some medications can affect energy or concentration, and knowing this helps "
        "ensure your working conditions suit your needs.\n\n"
        "Would you like to share your medication information?"
    ),
    "sick_leave": (
        "**Sick Leave History**\n\n"
        "This shows any periods you have taken off work due to illness. It can help "
        "identify patterns that point to conditions needing longer-term support or "
        "workplace adjustments.\n\n"
        "Would you like to share your sick leave history?"
    ),
}

_CONSENT_KEYS = list(CONSENT_LABELS.keys())

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


def advance_consent_step() -> None:
    """Move to the next consent category, or end the guided flow."""
    st.session_state.consent_step += 1
    if st.session_state.consent_step < len(_CONSENT_KEYS):
        next_type = _CONSENT_KEYS[st.session_state.consent_step]
        st.session_state.display_history.append({
            "role": "assistant",
            "text": CATEGORY_EXPLANATIONS[next_type],
        })
    else:
        st.session_state.awaiting_consent = False
        n = len(st.session_state.fetched_types)
        if n > 0:
            closing = (
                f"Thank you for going through each category — you have shared "
                f"{n} category{'s' if n != 1 else ''} of data. "
                "You can ask me any questions about your records below."
            )
        else:
            closing = (
                "Thank you. You have chosen not to share any health data at this time. "
                "You can still ask questions, though I won't be able to access your records."
            )
        st.session_state.display_history.append({"role": "assistant", "text": closing})


def reset_for_person(person_id: str, person_name: str) -> None:
    st.session_state.person_id        = person_id
    st.session_state.person_name      = person_name
    st.session_state.display_history  = []
    st.session_state.fetched_types    = set()
    st.session_state.consent_step     = 0
    st.session_state.awaiting_consent = True
    st.session_state.reset_count      = st.session_state.get("reset_count", 0) + 1
    for dt in CONSENT_LABELS:
        try:
            registry.revoke(dt)
        except ValueError:
            pass


# ── Session state ──────────────────────────────────────────────────────────────

for _key, _default in [
    ("person_id",        None),
    ("person_name",      None),
    ("display_history",  []),
    ("fetched_types",    set()),
    ("consent_step",     0),
    ("awaiting_consent", True),
    ("reset_count",      0),
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

# Pre-populate checkbox session state from fetched_types before the sidebar renders.
# Streamlit silently ignores st.session_state[key] = value when set after a widget
# has already been instantiated in the current run, so this must happen here.
if st.session_state.awaiting_consent:
    for _dt in st.session_state.fetched_types:
        st.session_state[f"cb_{_dt}_{st.session_state.reset_count}"] = True

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
        st.markdown("**Your health data**")
        st.caption("Answer each question in the chat to share your data.")
        st.caption("You can tick or untick categories at any time.")

        for data_type, label in CONSENT_LABELS.items():
            # Disabled during the guided flow to keep the chat as the single input
            checked = st.checkbox(
                label,
                key=f"cb_{data_type}_{st.session_state.reset_count}")
            # After guided flow, checkboxes are live — sync to registry
            if not st.session_state.awaiting_consent:
                if checked:
                    registry.grant(data_type)
                else:
                    try:
                        registry.revoke(data_type)
                    except ValueError:
                        pass

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

# Generate opening message + first category question on first load
if not st.session_state.display_history:
    with st.spinner(""):
        try:
            opening = get_opening_message(tools.get_person_info(st.session_state.person_id))
        except Exception as exc:
            st.error(f"Could not reach LLM proxy: {exc}")
            st.stop()
    st.session_state.display_history.append({"role": "assistant", "text": opening})
    st.session_state.display_history.append({
        "role": "assistant",
        "text": CATEGORY_EXPLANATIONS[_CONSENT_KEYS[0]],
    })

# After guided flow: fetch any newly ticked sidebar categories
if not st.session_state.awaiting_consent:
    newly_fetched = False
    for data_type in _CONSENT_KEYS:
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

# Display conversation history
for msg in st.session_state.display_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

# ── Guided consent flow: Yes / No buttons ──────────────────────────────────────

if st.session_state.awaiting_consent:
    data_type = _CONSENT_KEYS[st.session_state.consent_step]
    label     = CONSENT_LABELS[data_type]
    cb_key    = f"cb_{data_type}_{st.session_state.reset_count}"

    col_yes, col_no, _ = st.columns([1, 1, 4])

    with col_yes:
        if st.button("✅ Yes, share this", key=f"yes_{data_type}_{st.session_state.reset_count}"):
            # Record the user's decision
            st.session_state.display_history.append({"role": "user", "text": f"Yes — share my {label}."})

            # Auto-tick the sidebar checkbox
            st.session_state.cb_key = True
            registry.grant(data_type)

            # Fetch and display inline so ordering is correct
            with st.spinner(f"Retrieving {label}…"):
                try:
                    data_text = fetch_data(
                        st.session_state.person_id,
                        st.session_state.person_name,
                        data_type,
                        tools,
                    )
                except Exception as exc:
                    data_text = f"Could not retrieve {label}: {exc}"

            st.session_state.display_history.append({"role": "assistant", "text": data_text})
            st.session_state.fetched_types.add(data_type)
            advance_consent_step()
            st.rerun()

    with col_no:
        if st.button("❌ No, skip this", key=f"no_{data_type}_{st.session_state.reset_count}"):
            st.session_state.display_history.append({"role": "user", "text": f"No — skip my {label}."})
            advance_consent_step()
            st.rerun()

# ── Chat Q&A (only available after the guided flow is complete) ────────────────

else:
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
