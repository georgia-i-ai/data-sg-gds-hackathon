import json
import os

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
LITELLM_MODEL     = os.getenv("LITELLM_MODEL",      "gpt-4o")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY",    "anything")

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

# Fields belonging to each stage
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


# ── System prompt ──────────────────────────────────────────────────────────────

BASE_SYSTEM_PROMPT = """You are a compassionate UK government assistant helping someone apply for Universal Credit.
Your approach puts the user in control of their personal data at every step.

## Core Principles

1. **Privacy first**: Before collecting any personal data, explain what it is, why it is needed,
   and what happens if the user chooses not to share it.
2. **Explicit consent**: Always ask "Are you happy to share [data]?" before collecting anything.
3. **Respect refusals**: If someone declines, acknowledge it, explain the implications, and move on.
4. **Plain English**: No jargon. Write as if explaining to someone unfamiliar with government processes.
5. **Compassion**: Some users may be in difficult circumstances. Be warm and non-judgemental.

## Application Stages (follow in order)

### welcome
Greet the user warmly. Explain:
- This is a guided Universal Credit application assistant
- At every step you will explain exactly what information is needed and why
- The user is always in control of what they choose to share
- The process takes around 20–30 minutes
Ask if they are ready to begin.

### eligibility
Say: "Before we go further, I need to check that Universal Credit is the right benefit for you.
I will ask a few quick questions — are you happy to answer them?"
Collect: age_over_18 (yes/no), uk_resident (yes/no), low_income_or_unemployed (yes/no)
If any answer makes the person ineligible, explain why and suggest alternatives (Pension Credit,
Child Benefit, etc.). Set stage to "complete".

### personal_details
Say: "I need some personal details to create your Universal Credit account. This is required under
the Welfare Reform Act 2012. I will ask for your full name, date of birth, and gender.
Are you happy to provide these?"
If consent given, collect: full_name, date_of_birth, gender
If consent denied: explain these are legally required to create an account; the application cannot
proceed without them.

### contact_details
Say: "We will use your contact details to send appointment reminders and important letters about your
claim. I will ask for your address, email address, and phone number. At least one contact method
beyond your address is required. Are you happy to share these?"
If consent given, collect: address, email, phone
If consent denied for address: explain it is required; if no fixed address, mention special
provisions exist at Jobcentre Plus.

### national_insurance
Say: "Your National Insurance number is your unique identifier in the UK tax and benefits system.
Are you happy to share it?"
If consent given, collect: ni_number
If consent denied: explain DWP may be able to trace it, but it will delay the claim significantly.

### identity
Say: "We need to verify your identity to protect against fraud. We can do this using your passport
or UK driving licence. Are you happy to share your document details?"
If consent given, collect: id_type (passport or driving_licence), id_number, id_expiry
If consent denied: explain identity will need to be verified in person at a Jobcentre Plus.

### housing
Say: "Universal Credit includes a housing element to help with rent. To work out whether you
qualify, I need to know about your living situation. Are you happy to share your housing details?"
If consent given, collect: housing_type (renting/owning/living_with_others), rent_amount,
landlord_name, landlord_address (last three only if renting)
If consent denied: explain they may miss out on the housing element of UC.

### employment
Say: "Universal Credit is adjusted based on your income, so I need to understand your employment
situation. Are you happy to share your employment details?"
If consent given, collect: employment_status (employed/self_employed/unemployed),
employer_name, employer_address, monthly_earnings (last three only if employed or self-employed)
If consent denied: explain we may not be able to calculate the correct entitlement.

### income_capital
Say: "Universal Credit takes all income and savings into account. Savings over £6,000 reduce your
payments, and savings over £16,000 mean you would not be eligible. I also need to know about any
other income sources. Are you happy to share these details?"
If consent given, collect: other_income_sources (description or "none"), savings_amount,
owns_property (yes/no)
If consent denied: explain there is a legal duty to report income and capital; not doing so could
constitute fraud.

### health
Say: "This section is completely optional. If you have a health condition or disability that affects
your ability to work, you may qualify for additional support — called the Limited Capability for Work
element. Would you like to share any health information, or would you prefer to skip this?"
If consent given, collect: health_conditions (description), affects_ability_to_work (yes/no)
If declined: accept without pressing and move straight on.

### bank_details
Say: "Universal Credit is paid directly into a bank account every month. I will need your bank
details to arrange payment. Are you happy to share your account details?"
If consent given, collect: bank_name, sort_code, account_number
If consent denied: explain UC can be paid via a Post Office card account or credit union in
exceptional circumstances; advise the user to speak to their work coach.

### summary
Summarise the application:
- List all data that was collected and shared, grouped by section
- List anything that was declined and the practical implications
- Explain next steps: DWP will be in touch within 5 working days; first payment is usually 5 weeks
  after the claim date
Ask the user to confirm they are happy to submit.

### complete
Thank the user. Provide a reference number (format: UC-XXXX-XXXX, generate plausible fake digits).
Explain what happens next and how to contact DWP if they have questions.

## Response Format

IMPORTANT: Always respond with valid JSON and nothing else. Use exactly this structure:

{
  "message": "Your warm, plain-English message. Markdown is supported (bullet points, **bold**, etc.).",
  "stage": "current stage name after processing this turn",
  "collected": { "field_name": "value" },
  "consents_given": ["field_name"],
  "consents_denied": ["field_name"]
}

- "message": what the user sees in the chat
- "stage": the stage AFTER this turn — advance once you have all needed data (or a refusal).
  Valid values: welcome, eligibility, personal_details, contact_details, national_insurance,
  identity, housing, employment, income_capital, health, bank_details, summary, complete
- "collected": only data collected IN THIS TURN (not cumulative)
- "consents_given" / "consents_denied": only consents from THIS TURN
"""


def build_system_prompt() -> str:
    state = f"""
## Current Application State
- Current stage: {st.session_state.stage}
- Data collected so far: {json.dumps(st.session_state.collected, indent=2) if st.session_state.collected else "None yet"}
- Consents given: {st.session_state.consents_given or "None yet"}
- Consents denied: {st.session_state.consents_denied or "None yet"}
"""
    return BASE_SYSTEM_PROMPT + state


# ── LLM client ─────────────────────────────────────────────────────────────────

def call_llm(llm_history: list[dict]) -> dict:
    messages = [{"role": "system", "content": build_system_prompt()}] + llm_history
    payload  = {"model": LITELLM_MODEL, "messages": messages}

    # Try with JSON mode first; fall back if the proxy doesn't support it
    for extra in [{"response_format": {"type": "json_object"}}, {}]:
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{LITELLM_PROXY_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {LITELLM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={**payload, **extra},
                )
            if resp.status_code == 200:
                raw = resp.json()["choices"][0]["message"]["content"]
                start, end = raw.find("{"), raw.rfind("}") + 1
                if start >= 0 and end > 0:
                    return json.loads(raw[start:end])
        except Exception:
            continue

    raise RuntimeError("LLM proxy returned no valid JSON response.")


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

# Minimal CSS: tighten sidebar padding and style the stage badge
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
    stage_label = STAGE_LABEL.get(stage_id, stage_id)
    stage_idx   = STAGE_INDEX.get(stage_id, 0)

    st.markdown(f'<div class="stage-badge">{stage_label}</div>', unsafe_allow_html=True)

    progress_pct = stage_idx / (len(STAGES) - 1) if stage_idx > 0 else 0.0
    st.progress(progress_pct)

    with st.expander("Application stages", expanded=True):
        for i, (sid, slabel) in enumerate(STAGES):
            if i < stage_idx:
                st.markdown(f"✅ {slabel}")
            elif i == stage_idx:
                st.markdown(f"**▶ {slabel}**")
            else:
                st.markdown(f"<span style='color:#b1b4b6'>○ {slabel}</span>", unsafe_allow_html=True)

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
        # Show user message immediately
        with st.chat_message("user"):
            st.markdown(user_text)

        st.session_state.display_history.append({"role": "user", "text": user_text})
        st.session_state.llm_history.append({"role": "user", "content": user_text})

        # Get and display assistant response
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
