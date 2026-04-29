Fitness to Work Decision App

Problem

Build a app that has access to a database of health data (dummy in this case) that details gp appointments and medical investigations for a group of people. It will be an app for deciding on fitness to work,
  and will ask the user explicitedly before allowing an agent to retrieve the different types of data. First of all brainstorm how the consent model could work to ensure that the agent does not use the tools to retrieve data it has not
  been given consent for.

  ---
  Where consent is enforced

  Option A — Prompt-only (soft)
  Tell the LLM in the system prompt what it has consent to access. It decides whether to call a tool.
  - ❌ LLM can misinterpret, hallucinate consent, or be manipulated via prompt injection in retrieved data
  - Only acceptable as a third, belt-and-braces layer — not as the primary control

  Option B — Tool-level gating (hard, recommended)
  Each tool function checks a consent registry before doing anything. No consent → tool returns a denial, never the data.
  - ✅ Hard enforcement in Python — LLM cannot circumvent it even if instructed to
  - ✅ Works even if the model is confused or prompt-injected
  - ✅ Easy to audit every access attempt

  Option C — Conditional tool registration
  Only expose tools the user has consented to in the agent's tool list. Non-consented tools don't exist as far as the LLM is concerned.
  - ✅ Cleaner: the model can't even attempt a call it has no right to make
  - ⚠️  Needs a re-initialisation of the agent when consent changes mid-session
  - Best combined with B (belt and braces)

  Option D — Human-in-the-loop per call
  Every tool invocation is shown to the user before execution: "The assistant wants to retrieve your GP appointments — Allow / Deny?"
  - ✅ Maximum transparency, no ambiguity
  - ⚠️  Interrupts the flow significantly; likely too disruptive for a fitness-to-work context

  ---
  Granularity of consent

  Coarse — "Access to health data: yes/no"
  - Simple but doesn't reflect real data minimisation principles

  Per data category — consent per bucket: GP appointments / medical investigations / diagnoses / medications / mental health records
  - Maps naturally to how data is stored and how sensitive each category is
  - Mental health records are especially sensitive and should almost certainly be a separate, explicit consent gate

  Per field / per query — consent for each individual field or each individual query
  - Maximum control but probably unworkable UX in practice

  Recommended: per data category, with mental health as a mandatory separate gate.

  ---
  UI model for collecting consent

  Upfront, all-at-once
  Show all consent cards before the agent starts. User decides everything first, then the agent runs.
  - ✅ Simple flow, no interruptions
  - ⚠️  User may not understand why they need to consent to something until the context becomes clear

  Just-in-time
  The agent identifies what data it needs, pauses, asks the user, then proceeds if consented.
  - ✅ More intuitive — user sees the "why" in context
  - ⚠️  Requires the agent to declare intent before acting (structured output); adds latency

  Hybrid (likely best)
  Upfront consent for the broad categories, just-in-time confirmation before the agent actually calls a tool (one confirm step, not repeated questioning).

  ---
  Auditability

  Every tool call attempt — consented or not — should be logged with:
  - Timestamp
  - Data category requested
  - Whether consent existed at that moment
  - What was returned (or that it was denied)

  This is the basis for a data access audit trail, which is a legal requirement under UK GDPR for health data.

  ---
  Edge cases to handle

  - Partial consent: agent must degrade gracefully — make a decision with the data it has, flagging what it couldn't access
  - Revocation mid-session: if a user withdraws consent, already-retrieved data should be cleared from the agent's context
  - Scope creep: the agent should not be able to infer data it wasn't given (e.g. deduce a diagnosis from appointment frequency)
  - Prompt injection in retrieved data: a malicious record shouldn't be able to instruct the agent to retrieve more data — the tool gate stops this

  ---