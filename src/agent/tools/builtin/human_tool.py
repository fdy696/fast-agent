"""Built-in human handoff tools.

Pydantic-ai exposed the plain function directly via ``agent.tool()``, so the
function signature IS the tool schema.  Options are modeled as a list of str
and the docstring becomes the tool description.
"""

from typing import Any


async def ask_human(
    question: str,
    context: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the user for clarification when required information is missing. Use this instead of guessing."""
    clean_options = [option for option in options or [] if option]
    return {
        "type": "human_input_required",
        "question": question,
        "context": context,
        "options": clean_options,
        "status": "pending",
    }
