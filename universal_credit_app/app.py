import json
import logging
import os

import httpx
import streamlit as st
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("uc_chat")

load_dotenv()

LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
LITELLM_MODEL     = os.getenv("LITELLM_MODEL",      "gpt-4o")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY",    "anything")

logger.info("Config — proxy: %s  model: %s", LITELLM_PROXY_URL, LITELLM_MODEL)

# ── Static config ──────────────────────────────────────────────────────────────

STAGES = [
    ("welcome",            "Welcome"),
    ("eligibility",        "Eligibility check"),
    ("personal_details",   "Personal details"),
    ("contact_details",    "Contact details"),
    ("national_insurance", "National Insurance"),
    ("identity",           "Identity verification"),
    ("housing",            "Housing situation"),
    ("employment",         "Employment"),
    ("income_capital",     "Income & capital"),
    ("health",             "Health (optional)"),
    ("bank_details",       "Bank details"),
    ("summary",            "Summary"),
    ("complete",           "Complete"),
]

STAGE_LABEL = dict(STAGES)
STAGE_INDEX = {sid: i for i, (sid, _) in enumerate(STAGES)}

STAGE_FIELDS: dict[str, list[str]] = {
    "eligibility":        ["age_over_18", "uk_resident", "low_income_or_unemployed"],
    "personal_details":   ["full_name", "date_of_birth", "gender"],
    "contact_details":    ["address", "email", "phone"],
    "national_insurance": ["ni_number"],
    "identity":           ["id_type", "id_number", "id_expiry"],
    "housing":            ["housing_type", "rent_amount", "landlord_name", "landlord_address"],
    "employment":         ["employment_status", "employer_name", "employer_address", "monthly_earnings"],
    "income_capital":     ["other_income_sources", "savings_amount", "owns_property"],
    "health":             ["health_conditions", "affects_ability_to_work"],
    "bank_details":       ["bank_name", "sort_code", "account_number"],
}

FIELD_LABEL: dict[str, str] = {
    "age_over_18":             "Age 18+",
    "uk_resident":             "UK resident",
    "low_income_or_unemployed":"Low income / unemployed",
    "full_name":               "Full name",
    "date_of_birth":           "Date of birth",
    "gender":                  "Gender",
    "address":                 "Address",
    "email":                   "Email",
    "phone":                   "Phone",
    "ni_number":               "NI number",
    "id_type":                 "ID type",
    "id_number":               "ID number",
    "id_expiry":               "ID expiry",
    "housing_type":            "Housing type",
    "rent_amount":             "Monthly rent",
    "landlord_name":           "Landlord name",
    "landlord_address":        "Landlord address",
    "employment_status":       "Employment status",
    "employer_name":           "Employer name",
    "employer_address":        "Employer address",
    "monthly_earnings":        "Monthly earnings",
    "other_income_sources":    "Other income",
    "savings_amount":          "Savings",
    "owns_property":           "Owns property",
    "health_conditions":       "Health conditions",
    "affects_ability_to_work": "Affects ability to work",
    "bank_name":               "Bank name",
    "sort_code":               "Sort code",
    "account_number":          "Account number",
}

MASKED_FIELDS = {"ni_number", "id_number", "sort_code", "account_number"}


def mask_value(field: str, value) -> str:
    s = "Yes" if value is True else "No" if value is False else str(value)
    if field not in MASKED_FIELDS:
        return s
    if field == "account_number":
        return "••••" + s[-4:]
    return "••••••••"


# ── Per-stage instructions ─────────────────────────────────────────────────────
# Only the instruction for the *current* stage is included in each system prompt.

STAGE_PROMPTS: dict[str, str] = {
    "welcome": """\
Greet the user warmly and introduce yourself as an assistant helping with their Universal Credit
application. Explain:
- At every step you will explain exactly what information is needed and why.
- The user is always in control of what they choose to share.
- The process takes around 20–30 minutes.
Ask if they are ready to begin. When they confirm, set stage to "eligibility".""",

    "eligibility": """\
Check whether the user is eligible for Universal Credit.
Fields to collect (check state — ask only for fields not yet collected):
- age_over_18 (yes/no): must be 18 or over
- uk_resident (yes/no): must be living in the UK
- low_income_or_unemployed (yes/no): must be on a low income or out of work

These are eligibility questions, not personal data — no consent needed, just explain why you are asking.
Ask for ONE field per message.
If any answer makes the person ineligible, explain why, suggest alternatives (Pension Credit for
over-66s, Child Benefit, etc.), and set stage to "complete".
When all three are collected and the person is eligible, set stage to "personal_details".""",

    "personal_details": """\
Collect the user's personal details to create their Universal Credit account.
Fields to collect (check state — ask only for fields not yet collected):
- full_name
- date_of_birth
- gender

Before starting: explain these are required under the Welfare Reform Act 2012, then ask for consent.
If consent denied: explain these are legally required and the application cannot proceed without them.
If consent given: collect ONE field per message.
When all three are collected (or consent refused), set stage to "contact_details".""",

    "contact_details": """\
Collect contact details used for appointment reminders and letters about the claim.
Fields to collect (check state — ask only for fields not yet collected):
- address (required)
- email
- phone (at least one of email/phone is required)

Before starting: explain the purpose, then ask for consent to give these details.
If consent denied for address: explain it is required; mention Jobcentre Plus provisions for
people without a fixed address.
Collect ONE field per message.
When done, set stage to "national_insurance".""",

    "national_insurance": """\
Collect the user's National Insurance number.
Field to collect: ni_number

Explain: the NI number is their unique identifier in the UK tax and benefits system.
Ask for consent. If denied: explain DWP may trace it but it will significantly delay the claim.
When done (collected or declined), set stage to "identity".""",

    "identity": """\
Verify the user's identity to prevent fraud.
Fields to collect (check state — ask only for fields not yet collected):
- id_type (passport or driving_licence)
- id_number
- id_expiry

Ask for consent before starting. If denied: explain identity must be verified in person at
a Jobcentre Plus.
If consent given: collect id_type first, then id_number, then id_expiry — ONE per message.
When done, set stage to "housing".""",

    "housing": """\
Understand the user's housing situation to calculate the UC housing element.
Fields to collect (check state — ask only for fields not yet collected):
- housing_type (renting / owning / living_with_others)
- rent_amount       — only if renting
- landlord_name     — only if renting
- landlord_address  — only if renting

Ask for consent, explaining the housing element of UC. If denied: explain they may miss out on it.
If consent given: ask for housing_type first. If renting, continue with the rental fields ONE per
message. Skip rental fields if they are not renting.
When done, set stage to "employment".""",

    "employment": """\
Understand the user's employment situation to calculate their UC entitlement.
Fields to collect (check state — ask only for fields not yet collected):
- employment_status (employed / self_employed / unemployed)
- employer_name     — only if employed
- employer_address  — only if employed
- monthly_earnings  — only if employed or self_employed

Ask for consent, explaining UC is means-tested. If denied: explain entitlement cannot be
calculated accurately.
If consent given: ask for employment_status first, then employer details if applicable — ONE per message.
When done, set stage to "income_capital".""",

    "income_capital": """\
Collect details of the user's other income and savings.
Fields to collect (check state — ask only for fields not yet collected):
- other_income_sources (description, or "none")
- savings_amount
- owns_property (yes/no)

Before starting: explain the £6,000 and £16,000 savings thresholds, then ask for consent.
If denied: explain there is a legal duty to report all income and capital; not doing so could
constitute fraud.
If consent given: collect ONE field per message.
When done, set stage to "health".""",

    "health": """\
This section is entirely optional.
Fields to collect if the user wants to share:
- health_conditions (description)
- affects_ability_to_work (yes/no)

Explain: people with a health condition or disability affecting their ability to work may qualify
for the Limited Capability for Work element (extra payments). Make it very clear this is optional
and they can skip it.
If they want to skip: set stage to "bank_details" immediately without collecting anything.
If they want to share: collect ONE field per message.
When done, set stage to "bank_details".""",

    "bank_details": """\
Collect the user's bank details to pay Universal Credit.
Fields to collect (check state — ask only for fields not yet collected):
- bank_name
- sort_code
- account_number

Ask for consent, explaining UC is paid monthly into a bank account. If denied: explain UC can be
paid via a Post Office card account or credit union in exceptional circumstances; advise speaking
to a work coach.
If consent given: collect ONE field per message.
When done, set stage to "summary".""",

    "summary": """\
Produce a clear summary of the completed application:
- List all data collected, grouped by section.
- List any information that was declined and the practical implications.
- Explain next steps: DWP will be in touch within 5 working days; first payment is usually
  5 weeks after the claim date.
Ask the user to confirm they are happy to submit. When they confirm, set stage to "complete".""",

    "complete": """\
Thank the user warmly. Generate a plausible reference number (format: UC-XXXX-XXXX).
Explain what happens next:
- DWP will review the application and may ask for supporting documents.
- They may be invited to a Jobcentre Plus appointment.
- First payment is usually 5 weeks after the claim date.
- They can manage their claim via the Universal Credit online journal.
Provide the DWP helpline: 0800 328 5644 (free to call, Monday–Friday 8am–6pm).""",
}

# ── System prompt ──────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """\
You are a compassionate UK government assistant helping someone apply for Universal Credit.
Your approach puts the user in control of their personal data at every step.

## Core Principles

1. **Privacy first**: Before collecting any personal data, explain what it is needed for and what
   happens if the user chooses not to share it.
2. **Explicit consent**: Always ask for consent before collecting personal data.
3. **Respect refusals**: If someone declines, acknowledge it, explain the implications, and move on.
4. **One question at a time**: Only ask for ONE piece of information per message. Check the current
   application state to see what has already been collected, then ask for the next item.
5. **Plain English**: No jargon. Write clearly for someone unfamiliar with government processes.
6. **Compassion**: Some users may be in difficult circumstances. Be warm and non-judgemental.

## Response Format

IMPORTANT: Always respond with valid JSON and nothing else. Use exactly this structure:

{
  "message": "Your warm, plain-English message. Markdown is supported.",
  "stage": "stage name after this turn",
  "collected": { "field_name": "value" },
  "consents_given": ["field_name"],
  "consents_denied": ["field_name"]
}

- "message": what the user sees in the chat
- "stage": the stage AFTER this turn. Only advance when the current stage is fully complete
  (all fields collected or explicitly declined). Valid values: welcome, eligibility,
  personal_details, contact_details, national_insurance, identity, housing, employment,
  income_capital, health, bank_details, summary, complete
- "collected": only data collected IN THIS TURN (not cumulative)
- "consents_given" / "consents_denied": only from THIS TURN
"""


def build_system_prompt() -> str:
    stage = st.session_state.stage
    instruction = STAGE_PROMPTS.get(stage, "Continue the application.")
    already = (
        json.dumps(st.session_state.collected, indent=2)
        if st.session_state.collected else "none"
    )
    return (
        BASE_SYSTEM_PROMPT
        + f"\n## Current Stage: {stage}\n\n"
        + instruction
        + f"""

## Application State (already collected this session — do not ask for these again)
- Data collected: {already}
- Consents given:  {st.session_state.consents_given or "none"}
- Consents denied: {st.session_state.consents_denied or "none"}
"""
    )


# ── LLM client ─────────────────────────────────────────────────────────────────

def call_llm(llm_history: list[dict]) -> dict:
    messages = [{"role": "system", "content": build_system_prompt()}] + llm_history
    payload  = {"model": LITELLM_MODEL, "messages": messages}

    # 5 s to connect, 60 s to receive the full response
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)

    for attempt, extra in enumerate([{"response_format": {"type": "json_object"}}, {}], start=1):
        label = "json_mode" if attempt == 1 else "plain"
        logger.debug("LLM attempt %d (%s): POST %s/chat/completions model=%s history_len=%d",
                     attempt, label, LITELLM_PROXY_URL, LITELLM_MODEL, len(llm_history))
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{LITELLM_PROXY_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {LITELLM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={**payload, **extra},
                )
            logger.debug("LLM attempt %d: HTTP %d", attempt, resp.status_code)
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"]
                logger.debug("LLM raw response (%d chars): %s", len(raw), raw[:200])
                start, end = raw.find("{"), raw.rfind("}") + 1
                if start >= 0 and end > 0:
                    parsed = json.loads(raw[start:end])
                    logger.info("LLM response parsed OK: stage=%s collected_keys=%s",
                                parsed.get("stage"), list((parsed.get("collected") or {}).keys()))
                    return parsed
            else:
                logger.warning("LLM attempt %d: non-200 body: %s", attempt, resp.text[:300])
        except httpx.ConnectError as exc:
            logger.error("LLM attempt %d: connection refused — is the proxy running at %s? (%s)",
                         attempt, LITELLM_PROXY_URL, exc)
            raise RuntimeError(
                f"Cannot reach LLM proxy at {LITELLM_PROXY_URL}. "
                "Check LITELLM_PROXY_URL in your .env and that the proxy is running."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("LLM attempt %d: timed out (%s)", attempt, exc)
        except Exception as exc:
            logger.exception("LLM attempt %d: unexpected error: %s", attempt, exc)

    raise RuntimeError("LLM proxy returned no valid JSON response after both attempts.")


def apply_parsed(parsed: dict) -> str:
    """Update session state from a parsed LLM response; return the display message."""
    st.session_state.stage = parsed.get("stage", st.session_state.stage)
    st.session_state.collected.update(parsed.get("collected") or {})
    st.session_state.consents_given.extend(parsed.get("consents_given") or [])
    st.session_state.consents_denied.extend(parsed.get("consents_denied") or [])
    return parsed.get("message", "")


# ── Session state initialisation ───────────────────────────────────────────────

for _key, _default in [
    ("llm_history",      []),
    ("display_history",  []),
    ("stage",            "welcome"),
    ("collected",        {}),
    ("consents_given",   []),
    ("consents_denied",  []),
]:
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Apply for Universal Credit",
    page_icon="🇬🇧",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] { min-width: 280px; max-width: 320px; }
    .stage-badge {
        display: inline-block;
        background: #1d70b8;
        color: white;
        font-size: 13px;
        font-weight: 600;
        padding: 4px 10px;
        border-radius: 4px;
        margin-bottom: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## Your application")

    stage_id    = st.session_state.stage
    stage_idx   = STAGE_INDEX.get(stage_id, 0)

    st.markdown(
        f'<div class="stage-badge">{STAGE_LABEL.get(stage_id, stage_id)}</div>',
        unsafe_allow_html=True,
    )

    progress_pct = stage_idx / (len(STAGES) - 1) if stage_idx > 0 else 0.0
    st.progress(progress_pct)

    with st.expander("Application stages", expanded=True):
        for i, (sid, slabel) in enumerate(STAGES):
            if i < stage_idx:
                st.markdown(f"✅ {slabel}")
            elif i == stage_idx:
                st.markdown(f"**▶ {slabel}**")
            else:
                st.markdown(
                    f"<span style='color:#b1b4b6'>○ {slabel}</span>",
                    unsafe_allow_html=True,
                )

    st.divider()
    st.markdown("**Information shared**")

    given_set  = set(st.session_state.consents_given)
    denied_set = set(st.session_state.consents_denied)
    has_any    = False

    for section_id, fields in STAGE_FIELDS.items():
        visible = [
            f for f in fields
            if st.session_state.collected.get(f) is not None
            or f in given_set
            or f in denied_set
        ]
        if not visible:
            continue

        has_any = True
        st.markdown(f"**{STAGE_LABEL.get(section_id, section_id)}**")
        for field in visible:
            label = FIELD_LABEL.get(field, field)
            if st.session_state.collected.get(field) is not None:
                val = mask_value(field, st.session_state.collected[field])
                st.markdown(f"✅ {label}: `{val}`")
            elif field in denied_set:
                st.markdown(f"❌ {label}: *not shared*")
            elif field in given_set:
                st.markdown(f"✅ {label}")

    if not has_any:
        st.caption("Nothing shared yet.")

# ── Main chat area ─────────────────────────────────────────────────────────────

st.markdown(
    "<h2 style='margin-bottom:0'>Apply for Universal Credit</h2>"
    "<p style='color:#505a5f;margin-top:4px'>A privacy-first guided application</p>",
    unsafe_allow_html=True,
)
st.divider()

# Generate the opening welcome message on first load
if not st.session_state.display_history:
    with st.spinner("Starting your application…"):
        try:
            parsed       = call_llm([{"role": "user", "content": "[SYSTEM: Begin with a warm welcome message.]"}])
            welcome_text = apply_parsed(parsed)
            st.session_state.display_history.append({"role": "assistant", "text": welcome_text})
            st.session_state.llm_history.append({"role": "assistant", "content": json.dumps(parsed)})
        except Exception as exc:
            st.error(f"Could not reach the LLM proxy — check your .env settings. ({exc})")
            st.stop()

# Replay conversation history
for msg in st.session_state.display_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

# Handle new user input
if st.session_state.stage != "complete":
    if user_text := st.chat_input("Type your response…"):
        with st.chat_message("user"):
            st.markdown(user_text)

        st.session_state.display_history.append({"role": "user", "text": user_text})
        st.session_state.llm_history.append({"role": "user", "content": user_text})

        with st.chat_message("assistant"):
            placeholder = st.empty()
            placeholder.markdown("*Thinking…*")
            try:
                parsed        = call_llm(st.session_state.llm_history)
                response_text = apply_parsed(parsed)
            except Exception as exc:
                response_text = f"Sorry, something went wrong — {exc}. Please try again."
                parsed        = {}

            placeholder.markdown(response_text)

        st.session_state.display_history.append({"role": "assistant", "text": response_text})
        st.session_state.llm_history.append({"role": "assistant", "content": json.dumps(parsed)})

        st.rerun()
else:
    st.success("Your application has been submitted successfully.")
    if st.button("Start a new application"):
        for key in ["llm_history", "display_history", "stage", "collected", "consents_given", "consents_denied"]:
            del st.session_state[key]
        st.rerun()
