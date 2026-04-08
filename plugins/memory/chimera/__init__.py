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


# Session context — AAAK-style keyword extraction from recent messages
_SESSION_MESSAGES = []  # rolling buffer of recent user messages
_SESSION_MAX_MSGS = 10  # keep last N user messages
_SESSION_KEYWORDS = []  # cached top keywords

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
        title = r.title or str(Path(r.path).stem).replace("_", " ")
        date_str = f" ({r.date})" if r.date else ""
        imp_str = f" imp:{r.importance}" if r.importance != 50 else ""
        summary = r.summary[:140] if r.summary else ""
        # Format: › Title [TIER] (89%) 2026-04-07 imp:85
        #           summary text here...
        #           path/to/file.md
        lines.append(f"\u203a {title} [{tier}] ({pct}%){date_str}{imp_str}")
        if summary:
            lines.append(f"  {summary}")
        lines.append(f"  \u2192 {r.path}")
        paths.append(r.path)
    lines.append('Use chimera_expand("path") to load full content.')
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
        # Reset session context for fresh session
        global _SESSION_MESSAGES, _SESSION_KEYWORDS
        _SESSION_MESSAGES = []
        _SESSION_KEYWORDS = []

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
            # Enrich query with session context (from periodic flush writes)
            session_ctx = self._read_session_context()
            enriched_query = query.strip()
            if session_ctx:
                enriched_query = f"{session_ctx}\n{enriched_query}"

            results = retrieve(enriched_query)
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
        """Feed user message to keyword extractor + pass to ByteRover for curation."""
        if not user_content or len(user_content.strip()) < 10:
            return

        # Update session keywords from user message (AAAK extraction)
        try:
            self._update_session_context(user_content)
        except Exception:
            pass

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

    @staticmethod
    def _extract_session_keywords(text: str, max_keywords: int = 15) -> list:
        """AAAK-style keyword extraction: frequency + proper noun boosting. Zero LLM."""
        import re as _re
        # Re-use stop words from indexer if available, else minimal set
        try:
            from chimera.indexer import STOP_WORDS as _SW
        except ImportError:
            _SW = {"the", "a", "an", "is", "are", "was", "were", "be", "have", "has",
                    "had", "do", "does", "did", "will", "would", "could", "should",
                    "can", "to", "of", "in", "for", "on", "with", "at", "by", "from",
                    "and", "but", "or", "if", "that", "this", "it", "its", "not",
                    "also", "just", "very", "really", "well", "back", "still", "way"}

        words = _re.findall(r"[a-zA-Z][a-zA-Z_-]{2,}", text)
        freq = {}
        for w in words:
            wl = w.lower()
            if wl in _SW or len(wl) < 3:
                continue
            freq[wl] = freq.get(wl, 0) + 1

        # Boost proper nouns + CamelCase + underscored terms (AAAK technique)
        for w in words:
            wl = w.lower()
            if wl in _SW:
                continue
            if w[0].isupper() and wl in freq:
                freq[wl] += 2
            if "_" in w or "-" in w or any(c.isupper() for c in w[1:]):
                if wl in freq:
                    freq[wl] += 2

        ranked = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in ranked[:max_keywords]]

    def _update_session_context(self, user_msg: str) -> None:
        """Add user message to rolling buffer and recompute keywords."""
        global _SESSION_MESSAGES, _SESSION_KEYWORDS
        _SESSION_MESSAGES.append(user_msg)
        if len(_SESSION_MESSAGES) > _SESSION_MAX_MSGS:
            _SESSION_MESSAGES = _SESSION_MESSAGES[-_SESSION_MAX_MSGS:]
        # Recompute keywords from all buffered messages
        combined = " ".join(_SESSION_MESSAGES)
        _SESSION_KEYWORDS = self._extract_session_keywords(combined)

    def _read_session_context(self) -> str:
        """Return cached session keywords as space-separated string."""
        global _SESSION_KEYWORDS
        return " ".join(_SESSION_KEYWORDS)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes to ByteRover."""
        if action not in ("add", "replace") or not content:
            return
        # Mirror to ByteRover (non-blocking)
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
