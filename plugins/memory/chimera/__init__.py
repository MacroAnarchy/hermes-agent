"""Chimera memory plugin — enhancement layer on ByteRover.

Adds BM25 keyword search, RRF rank fusion, relevance threshold
(inject nothing when nothing is relevant), and two-phase retrieval
(compact menu + agent expands what it needs).

ByteRover handles: storage, curation, semantic search (96.1% LoCoMo).
Chimera adds: BM25, RRF fusion, threshold, two-phase UI, truth tiers.

Config: memory.provider: chimera in config.yaml
No extra env vars needed — uses ByteRover's existing install.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# Add chimera to Python path
CHIMERA_DIR = Path.home() / ".hermes" / "chimera"
if str(CHIMERA_DIR.parent) not in sys.path:
    sys.path.insert(0, str(CHIMERA_DIR.parent))

# Import chimera modules
try:
    from chimera.retrieval import retrieve, ScoredResult
    from chimera.threshold import apply_threshold, score_to_percent
    from chimera.identity import load_identity
    from chimera.indexer import search_bm25, rebuild_index
    from chimera.tools import chimera_expand, chimera_store
    CHIMERA_AVAILABLE = True
except ImportError as e:
    logger.warning("Chimera modules not found: %s", e)
    CHIMERA_AVAILABLE = False


# ByteRover CLI resolution (reuse from byterover plugin)
_BRV_TIMEOUT = 120

def _resolve_brv() -> Optional[str]:
    candidates = [
        Path.home() / ".brv-cli" / "bin" / "brv",
        Path("/usr/local/bin/brv"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None

def _brv_cwd() -> str:
    return str(Path.home() / ".hermes" / "byterover")

def _run_brv(args: List[str], timeout: int = 10) -> dict:
    brv = _resolve_brv()
    if not brv:
        return {"success": False, "error": "brv not found"}
    cwd = _brv_cwd()
    Path(cwd).mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [brv] + args, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        if r.returncode == 0:
            return {"success": True, "output": r.stdout.strip()}
        return {"success": False, "error": r.stderr.strip() or f"exit {r.returncode}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"timeout {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Truth tiers
TIERS_PATH = Path.home() / ".chimera" / "truth_tiers.json"

def _load_tiers() -> dict:
    if TIERS_PATH.exists():
        try:
            return json.loads(TIERS_PATH.read_text())
        except Exception:
            pass
    return {}

def _get_tier(path: str) -> str:
    return _load_tiers().get(path, "evidence").upper()


# Menu formatting
def _format_menu(results: List[ScoredResult], max_items: int = 5) -> str:
    if not results:
        return ""
    results = results[:max_items]
    lines = ["\U0001f3db\ufe0f Memory scan:"]
    paths = []
    for r in results:
        pct = score_to_percent(r.score)
        tier = _get_tier(r.path)
        display = str(Path(r.path).parent)
        summary = (r.summary[:80] if r.summary else "")
        lines.append(f"\u203a {display} [{tier}] ({pct}%) \u2014 {summary}")
        paths.append(r.path)
    lines.append('Use chimera_expand("path") to load full content.')
    for p in paths:
        lines.append(f"  {p}")
    return "\n".join(lines)


# Tool schemas
EXPAND_SCHEMA = {
    "name": "chimera_expand",
    "description": (
        "Load full content of a memory from the palace scan. "
        "Use after seeing a path in the Memory scan menu."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path from the Memory scan menu",
            },
        },
        "required": ["path"],
    },
}

STORE_SCHEMA = {
    "name": "chimera_store",
    "description": (
        "Store a confirmed fact as canon-tier memory. Use for important "
        "information the user explicitly confirmed or corrected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to store permanently",
            },
        },
        "required": ["content"],
    },
}


class ChimeraMemoryProvider(MemoryProvider):
    """Chimera: ByteRover + BM25 + RRF + threshold + two-phase retrieval."""

    def __init__(self):
        self._session_id = ""
        self._identity = None
        self._sync_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "chimera"

    def is_available(self) -> bool:
        return CHIMERA_AVAILABLE and _resolve_brv() is not None

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._identity = load_identity() if CHIMERA_AVAILABLE else ""

    def system_prompt_block(self) -> str:
        return (
            "# Chimera Memory\n"
            "Active. Two-phase retrieval over ByteRover knowledge tree.\n"
            "Memory scan shows relevant topics — use chimera_expand(path) "
            "to load full content when needed. chimera_store(content) saves "
            "confirmed facts."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Two-phase retrieval: return compact menu, agent expands if needed."""
        if not CHIMERA_AVAILABLE or not query or len(query.strip()) < 10:
            return ""

        try:
            results = retrieve(query.strip())
            filtered = apply_threshold(results)
            if not filtered:
                return ""  # inject NOTHING — the key innovation

            menu = _format_menu(filtered)
            parts = [p for p in [self._identity, menu] if p]
            return "\n".join(parts)
        except Exception as e:
            logger.debug("Chimera prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str,
                  *, session_id: str = "") -> None:
        """Pass to ByteRover for curation (non-blocking)."""
        if not user_content or len(user_content.strip()) < 10:
            return

        def _sync():
            try:
                combined = f"User: {user_content[:2000]}\nAssistant: {assistant_content[:2000]}"
                _run_brv(["curate", "--", combined], timeout=_BRV_TIMEOUT)
            except Exception as e:
                logger.debug("Chimera sync failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="chimera-sync"
        )
        self._sync_thread.start()

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes to ByteRover."""
        if action not in ("add", "replace") or not content:
            return
        def _write():
            try:
                label = "User profile" if target == "user" else "Agent memory"
                _run_brv(["curate", "--", f"[{label}] {content}"], timeout=_BRV_TIMEOUT)
            except Exception:
                pass
        t = threading.Thread(target=_write, daemon=True, name="chimera-memwrite")
        t.start()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Flush to ByteRover before compression discards context."""
        if not messages:
            return ""
        parts = []
        for msg in messages[-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
                parts.append(f"{role}: {content[:500]}")
        if parts:
            combined = "\n".join(parts)
            def _flush():
                _run_brv(["curate", "--", f"[Pre-compression]\n{combined}"], timeout=_BRV_TIMEOUT)
            t = threading.Thread(target=_flush, daemon=True, name="chimera-flush")
            t.start()
        return ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [EXPAND_SCHEMA, STORE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if tool_name == "chimera_expand":
            path = args.get("path", "")
            if not path:
                return json.dumps({"error": "path is required"})
            try:
                result = chimera_expand(path)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": str(e)})

        elif tool_name == "chimera_store":
            content = args.get("content", "")
            if not content:
                return json.dumps({"error": "content is required"})
            try:
                result = chimera_store(content)
                return json.dumps({"result": result})
            except Exception as e:
                return json.dumps({"error": str(e)})

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def shutdown(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10.0)


def register(ctx) -> None:
    """Register Chimera as a memory provider plugin."""
    ctx.register_memory_provider(ChimeraMemoryProvider())
