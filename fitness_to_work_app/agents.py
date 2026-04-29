import json
import logging
import os

import httpx
from dotenv import load_dotenv

from fitness_to_work_app.tools import Tools

load_dotenv()

logger = logging.getLogger("ftw_agent")

LITELLM_PROXY_URL = os.getenv("LITELLM_PROXY_URL", "http://localhost:4000")
LITELLM_MODEL     = os.getenv("LITELLM_MODEL",      "gpt-4o")
LITELLM_API_KEY   = os.getenv("LITELLM_API_KEY",    "anything")

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


def run_agent(messages: list[dict], tools: Tools) -> str:
    """Run the agentic loop until the LLM returns a response with no tool calls.

    Mutates messages in-place so the caller retains the full conversation history,
    including intermediate tool calls and results.
    Returns the final text response.
    """
    while True:
        # Fetch schemas fresh each iteration — consent may have changed
        schemas = tools.get_schemas()

        logger.debug(
            "LLM call: %d messages, %d tools available", len(messages), len(schemas)
        )

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                f"{LITELLM_PROXY_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model":       LITELLM_MODEL,
                    "messages":    messages,
                    "tools":       schemas,
                    "tool_choice": "auto",
                },
            )

        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        messages.append(message)

        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            logger.debug("No tool calls — returning final response")
            return message.get("content", "")

        for tool_call in tool_calls:
            name      = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])

            logger.info("Tool call: %s(%s)", name, arguments)

            if hasattr(tools, name):
                result = getattr(tools, name)(**arguments)
            else:
                result = {"error": "unknown_tool", "message": f"No tool named '{name}'"}
                logger.warning("Unknown tool requested: %s", name)

            messages.append({
                "role":         "tool",
                "tool_call_id": tool_call["id"],
                "content":      json.dumps(result, default=str),
            })
