"""Safe, deliberately small placeholder substitution for lore content."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_content(content: str, values: Mapping[str, object]) -> str:
    """Replace known placeholders and leave unknown text intact.

    This is not a template engine: entries cannot execute expressions, access
    attributes, or call functions. That makes it predictable for public use.
    """

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return match.group(0) if value is None else str(value)

    return _PLACEHOLDER.sub(replace, content)


def standard_values(
    *,
    user_id: str,
    user_name: str,
    entry_name: str,
) -> dict[str, str]:
    user = f"{user_name}({user_id})" if user_name and user_id else user_name or user_id
    return {
        "user_id": user_id,
        "user_name": user_name,
        "user": user,
        "time": datetime.now().astimezone().strftime("%H:%M:%S"),
        "entry_name": entry_name,
    }
