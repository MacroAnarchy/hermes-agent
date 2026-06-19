"""Telegram Reaction Tool -- react to the last incoming message.

Self-registering tool that lets the agent add emoji reactions to the most
recent incoming Telegram message. Reads message_id from a state file written
by the Telegram adapter's on_processing_start hook (our custom patch).

Requires TELEGRAM_BOT_TOKEN in ~/.hermes/.env.
"""

import json
import logging
import os
from pathlib import Path

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_STATE_FILE = Path.home() / ".hermes" / "cache" / "telegram_last_msg.json"
_ENV_FILE = Path.home() / ".hermes" / ".env"


def _get_bot_token():
    """Extract TELEGRAM_BOT_TOKEN from .env."""
    try:
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_") and "TOKEN" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _get_last_message():
    """Read last incoming message chat_id + message_id from state file."""
    try:
        data = json.loads(_STATE_FILE.read_text())
        return data["chat_id"], str(data["message_id"])
    except Exception:
        return None


def telegram_react(emoji="🔥", message_id=None):
    """React to a Telegram message via the Bot API.

    Args:
        emoji: Emoji to react with (default 🔥).
        message_id: Optional explicit message_id. If omitted, reacts to the
                    last incoming message (from state file).
    """
    token = _get_bot_token()
    if not token:
        return tool_error("No TELEGRAM_BOT_TOKEN found in ~/.hermes/.env")

    # Resolve chat_id + message_id
    if message_id:
        last = _get_last_message()
        if not last:
            return tool_error("Cannot determine chat_id — no state file found")
        chat_id = last[0]
    else:
        last = _get_last_message()
        if not last:
            return tool_error("No recent message found. Send a message first.")
        chat_id, message_id = last

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{token}/setMessageReaction"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "message_id": str(message_id),
        "reaction": json.dumps([{"type": "emoji", "emoji": emoji}]),
    }).encode()

    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get("ok"):
            return tool_result(success=True, message=f"Reacted {emoji} to message {message_id}")
        else:
            return tool_error(f"Telegram API error: {result.get('description', 'unknown')}")
    except Exception as e:
        return tool_error(f"Request failed: {e}")


REACT_SCHEMA = {
    "name": "telegram_react",
    "description": (
        "Add an emoji reaction to the last incoming Telegram message "
        "(or a specific message by ID). Use sparingly — only when a reaction "
        "genuinely fits the conversation. This is NOT an auto-ack; every "
        "reaction is intentional."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": "Emoji to react with (e.g. 🔥, 😂, 💀, ❤️, 👀). Default: 🔥",
            },
            "message_id": {
                "type": "string",
                "description": "Optional specific message ID to react to. If omitted, reacts to last incoming message.",
            },
        },
        "required": [],
    },
}


def _react_check():
    """Only show this tool if Telegram is configured."""
    return bool(_get_bot_token())


registry.register(
    name="telegram_react",
    toolset="hermes-telegram",
    schema=REACT_SCHEMA,
    handler=lambda args, **kw: telegram_react(
        emoji=args.get("emoji", "🔥"),
        message_id=args.get("message_id"),
    ),
    check_fn=_react_check,
    emoji="👁️",
)
