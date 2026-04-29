# Universal Credit Chat App

A privacy-first conversational assistant that guides a user through a Universal Credit application, asking explicit consent before collecting each piece of personal data.

---

## Overview

The app is built with **Streamlit** and calls an LLM via a **LiteLLM proxy**. It walks the user through the UC application in fixed stages, collecting one piece of information at a time. At every stage the assistant explains what data is needed and why, asks for explicit consent, and respects refusals — flagging the practical implications before moving on.

A sidebar shows the user's progress through the application and a live record of what information has been shared or declined.

---

## Running the app

Install dependencies and start the dev server:

```bash
uv sync
uv run streamlit run app.py
```

Configuration is via a `.env` file (copy `.env.example` to get started):

```
LITELLM_PROXY_URL=http://localhost:4000   # base URL of your LiteLLM proxy
LITELLM_MODEL=gpt-4o                      # model name as configured in the proxy
LITELLM_API_KEY=anything                  # proxy API key (if auth is enabled)
```

---

## Architecture

```
Streamlit (app.py)
  ├─ Sidebar          — stage progress, data shared / declined
  ├─ Chat area        — conversation with the LLM assistant
  └─ Session state    — all mutable state; never written by the LLM directly
        ├─ display_history   — friendly text for rendering chat bubbles
        ├─ llm_history       — raw JSON sent back to the LLM each turn
        ├─ stage             — current application stage
        ├─ collected         — data values gathered so far
        ├─ consents_given    — fields the user has agreed to share
        └─ consents_denied   — fields the user has declined to share

LiteLLM proxy  →  underlying LLM (e.g. GPT-4o, Claude)
```

### Two conversation histories

The app maintains two separate lists:

- **`llm_history`** — every turn stored as raw LLM JSON (including `stage`, `collected`, `consents_*`). This is what is sent to the model each turn so it has full context of the conversation.
- **`display_history`** — the `message` field only, used to render chat bubbles. Keeps the UI clean.

### System prompt assembly

Rather than sending a single large system prompt, `build_system_prompt()` assembles a focused prompt each turn:

1. **Base prompt** — persona, core principles, and the JSON response format (constant)
2. **Current stage instruction** — only the instruction for the active stage, drawn from `STAGE_PROMPTS`
3. **Current state** — what has already been collected and what consents have been given or denied

This keeps each prompt short and reduces the risk of the model losing track of instructions buried in a long document.

---

## Application stages

| Stage | Key data collected |
|---|---|
| Welcome | — |
| Eligibility check | Age, UK residency, income/employment status |
| Personal details | Full name, date of birth, gender |
| Contact details | Address, email, phone |
| National Insurance | NI number |
| Identity verification | Document type, number, expiry |
| Housing situation | Housing type, rent, landlord details |
| Employment | Employment status, employer, earnings |
| Income & capital | Other income, savings, property |
| Health (optional) | Health conditions, effect on work |
| Bank details | Bank name, sort code, account number |
| Summary | Review and confirm |
| Complete | Reference number issued |

---

## Consent model

The consent model operates at the **prompt level only** — the LLM is instructed to ask for consent and respect refusals, and the session state records what was agreed.

The LLM response format enforces structure:

```json
{
  "message": "...",
  "stage": "current_stage",
  "collected": { "field": "value" },
  "consents_given": ["field"],
  "consents_denied": ["field"]
}
```

`apply_parsed()` in Python updates `st.session_state` from the parsed response. The LLM never writes to session state directly.

> **Limitation:** this is a soft consent model. The LLM could in principle collect data without explicit consent if it misinterprets the instructions. For the fitness-to-work app (see `fitness-to-work-brainstorm.md`), a hard tool-level gate is used instead.

---

## Sensitive field masking

Certain fields are masked in the sidebar so that values are not displayed in full:

| Field | Display |
|---|---|
| NI number | `••••••••` |
| ID number | `••••••••` |
| Sort code | `••••••••` |
| Account number | `••••1234` (last 4 digits shown) |

---

## Key files

```
app.py                          — Streamlit app (single file)
pyproject.toml                  — dependencies (managed with uv)
.env.example                    — configuration template
docs/uc-chat-app.md             — this file
docs/fitness-to-work-brainstorm.md  — design notes for the second app
```
