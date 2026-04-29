# Fitness-to-Work Decision App — Consent Model Brainstorm

## Problem

Build an app that has access to a database of health data (dummy, for now) detailing GP appointments and medical investigations for a group of people. The app assists with fitness-to-work decisions and must ask the user explicitly before allowing an agent to retrieve each type of data. This document brainstorms how the consent model should work to ensure the agent cannot use tools to retrieve data it has not been given consent for.

---

## Where consent is enforced

### Option A — Prompt-only (soft)

Tell the LLM in the system prompt what it has consent to access. It decides whether to call a tool.

- ❌ LLM can misinterpret, hallucinate consent, or be manipulated via prompt injection in retrieved data
- Only acceptable as a third, belt-and-braces layer — not as the primary control

### Option B — Tool-level gating (hard, recommended)

Each tool function checks a consent registry before doing anything. No consent → tool returns a denial, never the data.

- ✅ Hard enforcement in Python — the LLM cannot circumvent it even if instructed to
- ✅ Works even if the model is confused or prompt-injected
- ✅ Easy to audit every access attempt

### Option C — Conditional tool registration

Only expose tools the user has consented to in the agent's tool list. Non-consented tools don't exist as far as the LLM is concerned.

- ✅ Cleaner: the model can't even attempt a call it has no right to make
- ⚠️ Requires re-initialisation of the agent when consent changes mid-session
- Best combined with Option B (belt and braces)

### Option D — Human-in-the-loop per call

Every tool invocation is shown to the user before execution: *"The assistant wants to retrieve your GP appointments — Allow / Deny?"*

- ✅ Maximum transparency, no ambiguity
- ⚠️ Interrupts the flow significantly; likely too disruptive for a fitness-to-work context

---

## Granularity of consent

**Coarse** — "Access to health data: yes/no"
- Simple but doesn't reflect real data minimisation principles

**Per data category** — consent per bucket: GP appointments / medical investigations / diagnoses / medications / mental health records
- Maps naturally to how data is stored and how sensitive each category is
- Mental health records are especially sensitive and should almost certainly require a separate, explicit consent gate

**Per field / per query** — consent for each individual field or each individual query
- Maximum control but probably unworkable UX in practice

**Recommended:** per data category, with mental health as a mandatory separate gate.

---

## UI model for collecting consent

**Upfront, all-at-once**
Show all consent cards before the agent starts. User decides everything first, then the agent runs.

- ✅ Simple flow, no interruptions
- ⚠️ User may not understand why they need to consent to something until the context becomes clear

**Just-in-time**
The agent identifies what data it needs, pauses, asks the user, then proceeds if consented.

- ✅ More intuitive — user sees the "why" in context
- ⚠️ Requires the agent to declare intent before acting (structured output); adds latency

**Hybrid (likely best)**
Upfront consent for the broad categories, plus a just-in-time confirmation immediately before the agent calls each tool — one confirm step per retrieval, not repeated questioning.

---

## Auditability

Every tool call attempt — consented or not — should be logged with:

- Timestamp
- Data category requested
- Whether consent existed at that moment
- What was returned (or that it was denied)

This forms the basis of a data access audit trail, which is a legal requirement under UK GDPR for special-category (health) data.

---

## Edge cases to handle

- **Partial consent:** the agent must degrade gracefully — make a decision with the data it has, clearly flagging what it could not access
- **Revocation mid-session:** if a user withdraws consent, already-retrieved data should be cleared from the agent's context
- **Scope creep:** the agent should not be able to infer data it was not given (e.g. deduce a diagnosis from appointment frequency)
- **Prompt injection in retrieved data:** a malicious record should not be able to instruct the agent to retrieve more data — the tool gate stops this

---

## Recommended architecture

```
UI (Streamlit)
  └─ Consent cards → writes to consent_registry (Python dict, UI-only writable)
        │
Agent (LLM)
  └─ Tool calls
        └─ Tool wrapper (Python)
              ├─ Checks consent_registry BEFORE fetching any data
              ├─ Logs attempt + outcome
              └─ Returns data  OR  "consent not granted for [category]"
```

The LLM is also told in the system prompt what consent currently exists (so it does not waste a call), but the tool wrapper is the authoritative enforcement point. The system prompt is advisory; the wrapper is mandatory.
