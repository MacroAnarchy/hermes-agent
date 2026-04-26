# Chimera v2: Session Search Enhancement Plugin

**Status:** Draft
**Date:** 2026-04-09
**Author:** Clawd + Tiger
**Target:** `plugins/memory/chimera/` rewrite + `tools/session_search_tool.py` enhancements

## Problem

Chimera v1 is an enhancement layer on ByteRover — a separate knowledge tree that
duplicates content already stored in `state.db` (985 sessions, FTS5-indexed). The
tree requires constant maintenance (daemon, curation, tree reorganization that
reverts), and the import pipeline is fragile (577 unimported Claude sessions).

The core insight: **session_search already does the heavy lifting** (FTS5 + LLM
summarization). ByteRover's tree is a parallel system that drifts from ground
truth and adds maintenance burden without proportional recall improvement.

## Architecture

```
state.db (single source of truth)
  └── session_search (FTS5 + LLM summarization)
        └── Chimera v2 plugin (menu scan, deep dive, context injection)
              └── Hermes MemoryProvider interface
```

**One source of truth** (state.db), **one retrieval backbone** (session_search),
**one enhancement layer** (Chimera v2 plugin).

## Two Modes

### 1. Menu Scan (cheap, proactive)

Triggered on every turn via `prefetch()`. Zero LLM calls — pure SQL.

```
SELECT topic, COUNT(*), MAX(started_at), SUM(message_count)
FROM sessions
GROUP BY topic
ORDER BY relevance_to_current_query
LIMIT 5-8
```

Outputs a compact menu injected into context:

```
🏛️ Memory scan:
› Career & BasicFit [EVIDENCE] (6 sessions, latest Apr 9)
  → 3 sessions about interview process, 2 about law school decision
› Quant Trading [EVIDENCE] (12 sessions, latest Mar 28)
  → EMA crossover, stat arb backtesting, signal engine
› Fiona & Relationships [EVIDENCE] (8 sessions, latest Feb 12)
Use session_search("topic") for full recall.
```

**How it works:**
1. Extract keywords from current conversation (AAAK — existing Chimera code)
2. FTS5 `MATCH` query with OR-joined keywords, `GROUP BY session_id`
3. Aggregate: session count, date range, source breakdown
4. Cluster by topic (using session titles + FTS5 snippet hits)
5. Format as compact menu

**Cost:** ~50ms SQL query, zero tokens. Runs on every turn.

### 2. Deep Dive (on demand, LLM-powered)

Triggered when the agent calls `session_search` directly, or when a menu item
is "double-clicked" (expanded via tool call).

This is the existing `session_search` tool enhanced with:
- **BM25 fusion:** Chimera's BM25 scorer reranks FTS5 results
- **Source filtering:** `source='claude-import'` for selective recall
- **Multi-session synthesis:** Instead of per-session summaries, optionally
  synthesize across all matching sessions into a topic-level summary

**Cost:** One LLM call per 3-5 sessions (existing behavior), ~500-2000 tokens.

## Plugin Structure

```
plugins/memory/chimera/
├── __init__.py          # ChimeraMemoryProvider (MemoryProvider ABC)
├── menu.py              # Menu scan logic (SQL queries, formatting)
├── retrieval.py         # BM25 reranking, fusion with FTS5 scores
├── keywords.py          # AAAK keyword extraction (from v1, keep as-is)
├── plugin.yaml          # Metadata
```

No external dependencies beyond what Hermes already has (SQLite, auxiliary LLM).

## MemoryProvider Implementation

```python
class ChimeraMemoryProvider(MemoryProvider):
    """Chimera v2: session_search enhancement with menu scan."""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Menu scan: cheap SQL-based topic overview."""
        keywords = extract_keywords(query)
        menu = build_menu(keywords, db=self.db)
        return format_menu(menu)

    def sync_turn(self, user_content: str, assistant_content: str, **kw) -> None:
        """No-op — session_search auto-indexes via Hermes session persistence."""
        pass

    def get_tool_schemas(self) -> list:
        """Expose session_search enhancement tools."""
        return [DEEP_DIVE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kw) -> str:
        if tool_name == "chimera_deep_dive":
            return deep_dive(args["topic"], source_filter=args.get("source"))
        ...
```

## Key Differences from v1

| Aspect | v1 (ByteRover) | v2 (Session Search) |
|--------|----------------|---------------------|
| Source of truth | Separate tree (3,994 files) | state.db (985 sessions) |
| Import pipeline | seed_claude, seed_tier2, progress tracking | None — auto-indexed |
| Maintenance | Daemon, tree reorg, _index.md | None |
| Menu scan | BM25 over filesystem tree | SQL over session metadata |
| Deep recall | brv query (black box) | session_search (FTS5 + LLM) |
| Storage cost | ~20MB + daemon process | ~0 (reuses existing DB) |
| Freshness | Requires curation queue | Instant — new sessions auto-indexed |
| Dependencies | Node.js brv CLI + daemon | Python stdlib only |

## Menu Scan Implementation Details

### Topic Clustering

Sessions don't have explicit "topics" — we derive them from:

1. **Session titles** — already auto-generated or set during import
2. **FTS5 snippet hits** — the matching text fragments show what a session is about
3. **Source grouping** — cluster by source (claude-import, telegram, cron)

For the menu, we don't need perfect clusters. A simple approach:
- Extract top 3-5 keywords per session from titles + first few messages
- Group sessions sharing 2+ keywords into the same menu item
- Show count, latest date, and a one-line summary

### Menu Format

```
🏛️ Memory scan:
› Topic Name [TIER] (N sessions, latest DATE)
  → Brief summary from title/snippet aggregation
› Another Topic [TIER] (N sessions, latest DATE)
  → Brief summary
Use session_search("topic") for full recall.
```

Tiers map to session sources:
- `CANON`: Explicitly confirmed facts (from chimera_store or memory tool)
- `EVIDENCE`: Direct session content
- `DERIVED`: Cross-session patterns (future: inductive summaries)

### Query Enrichment

The menu scan uses AAAK keyword extraction (kept from v1) to enrich the FTS5
query with session context. The last N user messages contribute keywords that
make the menu adaptive to the current conversation.

## Deep Dive Tool

New tool: `chimera_deep_dive`

```json
{
  "name": "chimera_deep_dive",
  "description": "Deep recall across all sessions for a topic. Returns synthesized summary of everything relevant. Use when menu scan shows relevant topics that need full context.",
  "parameters": {
    "type": "object",
    "properties": {
      "topic": {
        "type": "string",
        "description": "Topic or keywords to search across all sessions"
      },
      "source": {
        "type": "string",
        "description": "Filter by source: 'claude-import', 'telegram', 'all' (default)"
      },
      "limit": {
        "type": "integer",
        "description": "Max sessions to summarize (default 5)"
      }
    },
    "required": ["topic"]
  }
}
```

Implementation: wraps `session_search` with:
1. AAAK-enriched query
2. Optional source filter (e.g., only claude-import)
3. BM25 reranking of FTS5 results
4. Multi-session synthesis (single LLM call summarizing across sessions)

## Migration Path

1. **Create `plugins/memory/chimera_v2/`** alongside existing chimera plugin
2. **Implement menu scan** (SQL-only, no LLM — testable immediately)
3. **Implement deep dive** (wraps session_search)
4. **Add config toggle:** `memory.provider: chimera_v2`
5. **Test with existing data** — 985 sessions already in state.db
6. **Remove ByteRover dependency** — no more daemon, no more tree

### What Gets Removed

- `~/.hermes/byterover/` — entire ByteRover workspace
- `~/.hermes/chimera/` — v1 Chimera modules (indexer, retrieval, threshold)
- `plugins/memory/byterover/` — ByteRover plugin
- Import scripts (`seed_claude.py`, `seed_tier2.py`, etc.)

### What Gets Kept

- AAAK keyword extraction (from v1) — used for query enrichment
- Menu format and tier system — proven UX
- `chimera_expand` tool — repurposed as `chimera_deep_dive`
- `chimera_store` tool — maps to built-in memory tool (no separate backend)

## Open Questions

1. **Topic clustering quality** — Can we get good enough clusters from session
   titles + FTS5 snippets without embedding-based similarity? Or do we need
   lightweight embeddings for the menu?

2. **Cross-session synthesis** — Should deep dive always synthesize into one
   summary, or return per-session summaries like current session_search?

3. **Honcho integration** — Honcho's dialectic reasoning (inductive/deductive
   conclusions) is separate from this. Should Chimera v2 also query Honcho
   for derived insights, or stay pure session_search?

4. **Stale menu entries** — If the menu shows a topic that has no recent
   activity, should it age out? Recency weighting in the SQL query?

## Success Criteria

- Menu scan renders in <100ms on every turn
- Zero maintenance (no daemon, no tree, no import scripts)
- Recall quality >= current chimera v1 for common queries
- All 985 sessions immediately accessible (no import step)
- Plugin works on fresh Hermes install with just state.db
