"""
CNA Engine — RAG Pipeline for Strategy Retrieval
Semantic search over rules, doctrine lessons, and curated strategy
using ChromaDB + sentence-transformers embeddings.

Replaces the flat last-10 doctrine dump with situationally relevant
context retrieved at query time.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Embedding model — small, fast, CPU-only (~22MB)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ChromaDB collection name
COLLECTION_NAME = "cna_strategy"


class StrategyRAG:
    """
    Retrieval-Augmented Generation context provider.

    Indexes three knowledge sources:
    1. CNA rules (chunked by section headers)
    2. Doctrine lessons (deduplicated from logs/doctrine.jsonl)
    3. Strategic playbook (curated tactical knowledge)

    At query time, returns the most relevant chunks for the current
    game situation, replacing the flat last-10 doctrine dump.
    """

    def __init__(self, persist_dir: str = ".rag_index"):
        self.persist_dir = persist_dir
        self.collection = None
        self._client = None
        self._embed_fn = None
        self._available = False

    def build_index(
        self,
        rules_paths: list[str] | None = None,
        doctrine_path: str | None = None,
        playbook_path: str | None = None,
    ) -> bool:
        """
        Build or rebuild the ChromaDB index from knowledge sources.

        Returns True if index was built successfully, False if dependencies
        are missing or indexing failed.
        """
        try:
            import chromadb
            from chromadb.utils import embedding_functions
        except ImportError:
            logger.warning("RAG: chromadb not installed — RAG disabled")
            return False

        try:
            self._embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL,
            )
        except Exception as e:
            logger.warning("RAG: sentence-transformers not available: %s", e)
            return False

        try:
            self._client = chromadb.Client()  # in-memory, rebuilt each run
            # Delete existing collection if present (fresh rebuild)
            try:
                self._client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            self.collection = self._client.create_collection(
                name=COLLECTION_NAME,
                embedding_function=self._embed_fn,
            )
        except Exception as e:
            logger.error("RAG: ChromaDB initialization failed: %s", e)
            return False

        doc_count = 0

        # Index rules files
        if rules_paths:
            for path in rules_paths:
                if os.path.exists(path):
                    n = self._index_rules(path)
                    doc_count += n
                    logger.info("RAG: indexed %d chunks from %s", n, path)

        # Index doctrine lessons
        if doctrine_path and os.path.exists(doctrine_path):
            n = self._index_doctrine(doctrine_path)
            doc_count += n
            logger.info("RAG: indexed %d doctrine lessons from %s", n, doctrine_path)

        # Index strategic playbook
        if playbook_path and os.path.exists(playbook_path):
            n = self._index_playbook(playbook_path)
            doc_count += n
            logger.info("RAG: indexed %d playbook entries from %s", n, playbook_path)

        self._available = doc_count > 0
        logger.info("RAG: index built with %d total documents", doc_count)
        return self._available

    def query(
        self,
        query_text: str,
        side: str | None = None,
        phase: str | None = None,
        k: int = 5,
        min_playbook: int = 2,
    ) -> list[dict]:
        """
        Semantic search for relevant context with source diversity.

        Over-fetches and re-ranks to ensure at least `min_playbook` playbook
        entries appear in results (if that many exist), preventing doctrine
        volume from drowning out curated strategy.

        Args:
            query_text: Natural language query describing the situation.
            side: Optional side filter ("allied", "axis", or None for both).
            phase: Optional phase filter.
            k: Number of results to return.
            min_playbook: Minimum playbook entries to include if available.

        Returns:
            List of {text, source, side, score} dicts, sorted by relevance.
        """
        if not self._available or not self.collection:
            return []

        # Build where filter
        where_filter = None
        if side:
            where_filter = {
                "$or": [
                    {"side": side},
                    {"side": "general"},
                ]
            }

        # Over-fetch to ensure source diversity
        fetch_k = k * 3
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=fetch_k,
                where=where_filter,
            )
        except Exception as e:
            logger.warning("RAG: query failed: %s", e)
            return []

        if not results or not results.get("documents"):
            return []

        docs = results["documents"][0]
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
        distances = results["distances"][0] if results.get("distances") else [0.0] * len(docs)

        # Build candidate list
        candidates = []
        for doc, meta, dist in zip(docs, metadatas, distances):
            candidates.append({
                "text": doc,
                "source": meta.get("source", "unknown"),
                "side": meta.get("side", "general"),
                "score": round(1.0 - dist, 3) if dist <= 1.0 else round(dist, 3),
            })

        # Ensure source diversity: guarantee playbook and rules slots
        playbook = [c for c in candidates if c["source"].startswith("playbook:")]
        rules = [c for c in candidates if c["source"].startswith("rules:")]
        rest = [c for c in candidates if not c["source"].startswith("playbook:") and not c["source"].startswith("rules:")]

        selected: list[dict] = []
        selected_texts: set[str] = set()

        def _add(item: dict) -> bool:
            if item["text"] in selected_texts or len(selected) >= k:
                return False
            selected.append(item)
            selected_texts.add(item["text"])
            return True

        # Reserve slots: up to min_playbook from playbook, then 1 rules
        for item in playbook[:min_playbook]:
            _add(item)
        for item in rules[:1]:
            _add(item)

        # Fill remaining slots by overall relevance score
        for item in candidates:
            if len(selected) >= k:
                break
            _add(item)

        return selected[:k]

    def format_context(
        self,
        results: list[dict],
        max_chars: int = 1500,
    ) -> str:
        """
        Format RAG results into a prompt-injectable context string.

        Args:
            results: Output from query().
            max_chars: Maximum character budget.

        Returns:
            Formatted context string for prompt injection.
        """
        if not results:
            return ""

        lines = ["=== RELEVANT STRATEGY & RULES ==="]
        char_count = len(lines[0])

        for r in results:
            source_tag = r.get("source", "")
            text = r.get("text", "").strip()
            if not text:
                continue

            # Truncate individual chunks if very long
            if len(text) > 400:
                text = text[:397] + "..."

            line = f"[{source_tag}] {text}"
            if char_count + len(line) + 2 > max_chars:
                break
            lines.append(f"- {line}")
            char_count += len(line) + 4

        if len(lines) <= 1:
            return ""

        return "\n".join(lines)

    def add_documents(self, new_lessons: list[dict]) -> int:
        """
        Incrementally add new doctrine lessons to the index.
        Called after doctrine checkpoint extracts new lessons.

        Returns number of documents added.
        """
        if not self._available or not self.collection:
            return 0

        added = 0
        for lesson in new_lessons:
            text = lesson.get("lesson", "")
            if not text:
                continue

            doc_id = _make_id("doctrine", text)
            side = lesson.get("side", "general")

            try:
                self.collection.add(
                    documents=[text],
                    ids=[doc_id],
                    metadatas=[{
                        "source": "doctrine",
                        "side": side,
                        "category": lesson.get("category", ""),
                    }],
                )
                added += 1
            except Exception:
                pass  # Duplicate ID or other error

        if added:
            logger.info("RAG: added %d new doctrine documents", added)
        return added

    @property
    def is_available(self) -> bool:
        return self._available

    # ────────────────────────────────────
    # INDEXING HELPERS
    # ────────────────────────────────────

    def _index_rules(self, path: str) -> int:
        """Chunk a rules markdown file by ### headers and index."""
        with open(path, "r") as f:
            content = f.read()

        chunks = _chunk_by_headers(content)
        if not chunks:
            return 0

        source_name = os.path.basename(path).replace(".md", "")
        docs, ids, metas = [], [], []

        for i, chunk in enumerate(chunks):
            text = chunk["text"].strip()
            if len(text) < 20:
                continue
            doc_id = _make_id(source_name, text)
            docs.append(text)
            ids.append(doc_id)
            metas.append({
                "source": f"rules:{source_name}",
                "side": "general",
                "section": chunk.get("header", ""),
            })

        if docs:
            self.collection.add(documents=docs, ids=ids, metadatas=metas)
        return len(docs)

    def _index_doctrine(self, path: str) -> int:
        """Index deduplicated doctrine lessons, collapsing near-duplicates."""
        raw_lessons = []
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    raw_lessons.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("RAG: failed to read doctrine %s: %s", path, e)
            return 0

        if not raw_lessons:
            return 0

        # Collapse near-duplicate failure/success lessons that differ
        # only by count (e.g. "failed 5x" vs "failed 17x").
        # Keep the highest-count version per pattern.
        best_by_pattern: dict[str, dict] = {}
        for lesson in raw_lessons:
            text = lesson.get("lesson", "")
            if not text:
                continue
            key = _doctrine_pattern_key(text)
            count = _extract_count(text)
            prev = best_by_pattern.get(key)
            if prev is None or count > prev["_count"]:
                best_by_pattern[key] = {**lesson, "_count": count}

        lessons = list(best_by_pattern.values())

        docs, ids, metas = [], [], []
        for lesson in lessons:
            text = lesson["lesson"]
            doc_id = _make_id("doctrine", text)
            docs.append(text)
            ids.append(doc_id)
            metas.append({
                "source": "doctrine",
                "side": lesson.get("side", "general"),
                "category": lesson.get("category", ""),
            })

        self.collection.add(documents=docs, ids=ids, metadatas=metas)
        logger.info("RAG: doctrine collapsed %d → %d entries", len(raw_lessons), len(docs))
        return len(docs)

    def _index_playbook(self, path: str) -> int:
        """Index curated strategic playbook entries."""
        entries = []
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entries.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("RAG: failed to read playbook %s: %s", path, e)
            return 0

        if not entries:
            return 0

        docs, ids, metas = [], [], []
        for entry in entries:
            text = entry.get("text", "")
            if not text:
                continue
            topic = entry.get("topic", "")
            doc_id = _make_id("playbook", text)
            docs.append(text)
            ids.append(doc_id)
            metas.append({
                "source": f"playbook:{topic}",
                "side": entry.get("side", "general"),
                "phase": entry.get("phase", ""),
            })

        self.collection.add(documents=docs, ids=ids, metadatas=metas)
        return len(docs)


# ════════════════════════════════════════
# UTILITY FUNCTIONS
# ════════════════════════════════════════

def _make_id(source: str, text: str) -> str:
    """Generate a stable document ID from source + content hash."""
    h = hashlib.md5(text.encode()).hexdigest()[:12]
    return f"{source}_{h}"


# Pattern to strip counts from doctrine text for near-duplicate grouping.
_COUNT_RE = re.compile(r'\b(?:failed|succeeded|successful|effective)\s+\d+\s*(?:x|times|uses)\b', re.IGNORECASE)
_PARENS_COUNT_RE = re.compile(r'\(\d+\s*(?:x|successful uses)\)')


def _doctrine_pattern_key(text: str) -> str:
    """Normalize a doctrine lesson to a pattern key, stripping counts."""
    key = _COUNT_RE.sub("COUNT", text)
    key = _PARENS_COUNT_RE.sub("(COUNT)", key)
    # Collapse whitespace
    key = re.sub(r'\s+', ' ', key).strip().lower()
    return key


def _extract_count(text: str) -> int:
    """Extract the numeric count from a doctrine lesson for dedup ranking."""
    m = re.search(r'(\d+)\s*(?:x|times|uses)', text, re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _chunk_by_headers(text: str, max_chunk_chars: int = 2000) -> list[dict]:
    """
    Split markdown text into chunks by ### headers.
    Each chunk includes its header for context.
    """
    # Split by ### headers (level 3 and above)
    pattern = r'^(#{1,3}\s+.+)$'
    parts = re.split(pattern, text, flags=re.MULTILINE)

    chunks = []
    current_header = ""
    current_text = ""

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if re.match(r'^#{1,3}\s+', part):
            # Save previous chunk
            if current_text.strip():
                chunks.append({
                    "header": current_header,
                    "text": f"{current_header}\n{current_text}" if current_header else current_text,
                })
            current_header = part
            current_text = ""
        else:
            current_text += part + "\n"

    # Save last chunk
    if current_text.strip():
        chunks.append({
            "header": current_header,
            "text": f"{current_header}\n{current_text}" if current_header else current_text,
        })

    # Split oversized chunks
    final_chunks = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= max_chunk_chars:
            final_chunks.append(chunk)
        else:
            # Split by paragraphs
            paragraphs = text.split("\n\n")
            sub_text = ""
            for para in paragraphs:
                if len(sub_text) + len(para) > max_chunk_chars and sub_text:
                    final_chunks.append({
                        "header": chunk["header"],
                        "text": sub_text.strip(),
                    })
                    sub_text = para + "\n\n"
                else:
                    sub_text += para + "\n\n"
            if sub_text.strip():
                final_chunks.append({
                    "header": chunk["header"],
                    "text": sub_text.strip(),
                })

    return final_chunks


def build_expert_query(role: str, phase: str, state_signals: dict) -> str:
    """
    Build a semantic query string from expert role + phase + state signals.
    Used by ExpertAgent to query RAG before building prompts.

    Args:
        role: Expert role (ground, logistics, air, naval).
        phase: Phase type (movement_combat, fleet, air, etc.).
        state_signals: Dict with situational keys like:
            - units_in_contact (bool)
            - supply_critical (bool)
            - advancing (bool)
            - objective (str, nearest objective name)

    Returns:
        Natural language query string for semantic search.
    """
    parts = [f"{role} {phase}"]

    if state_signals.get("units_in_contact"):
        parts.append("units in contact combat engagement")
    if state_signals.get("supply_critical"):
        parts.append("critical supply shortage water fuel")
    if state_signals.get("advancing"):
        parts.append("advancing attack assault objective")
    if state_signals.get("retreating"):
        parts.append("retreat withdrawal fallback defensive")
    if state_signals.get("objective"):
        parts.append(f"objective {state_signals['objective']}")
    if state_signals.get("low_fuel"):
        parts.append("fuel shortage motorized")
    if state_signals.get("low_water"):
        parts.append("water shortage resupply truck")

    return " ".join(parts)


def build_situation_query(situation: str, role: str, signals) -> str:
    """
    Build a semantic query string for the SituationEngine.

    Maps StateSignals fields to search terms so the LLM gets relevant
    rules, doctrine, and playbook context for the current situation.

    Args:
        situation: Situation label (e.g. "ATTACK_PREPARED", "SUPPLY_CRISIS").
        role: Primary role (e.g. "front_line", "logistics", "air").
        signals: StateSignals dataclass instance.

    Returns:
        Natural language query string for semantic search.
    """
    # Situation-specific keywords to improve retrieval of curated playbook entries
    _SITUATION_KEYWORDS: dict[str, str] = {
        "ATTACK_PREPARED": "assault preparation barrage combined arms close assault fire support",
        "ADVANCE_OPPORTUNITY": "advance pursuit exploitation movement objective push forward",
        "FIGHTING_RETREAT": "retreat withdrawal fallback defensive position fortified",
        "GROUND_SUPPORT_URGENT": "close air support bombing strafing ground attack tactical mission",
        "CONVOY_INTERDICTION": "fleet sortie convoy interception recon naval strike",
        "SUPPLY_CRISIS": "supply resupply water fuel truck convoy port delivery",
        "AIR_SUPERIORITY_HELD": "air superiority bombing ground support strafing target selection",
        "AIR_PARITY": "air parity balanced CAP recon fighter patrol",
    }

    parts = [situation.replace("_", " ").lower(), role.replace("_", " ")]

    # Inject situation-specific keywords for better playbook matching
    if situation in _SITUATION_KEYWORDS:
        parts.append(_SITUATION_KEYWORDS[situation])

    if getattr(signals, "units_in_contact", 0) > 0:
        parts.append("combat engagement units in contact")
    if getattr(signals, "water_critical_count", 0) > 0 or getattr(signals, "any_zero_water", False):
        parts.append("water shortage resupply")
    if getattr(signals, "fuel_critical_count", 0) > 0 or getattr(signals, "any_zero_fuel", False):
        parts.append("fuel shortage motorized supply")
    advance = getattr(signals, "advance_or_retreat", "static")
    if advance == "advance":
        parts.append("offensive advance attack assault")
    elif advance == "retreat":
        parts.append("withdrawal defensive retreat fallback")
    if getattr(signals, "overextended", False):
        parts.append("overextended supply line")
    if getattr(signals, "objectives_contested", None):
        parts.append("contested objective " + " ".join(signals.objectives_contested[:2]))
    if getattr(signals, "best_assault_ratio", 0.0) >= 2.0:
        parts.append("favorable force ratio attack")

    # Air-specific mappings
    if role == "air" or "air" in situation.lower():
        ratio = getattr(signals, "air_superiority_ratio", 1.0)
        if ratio >= 2.0:
            parts.append("air superiority bombing ground support strafing")
        elif ratio <= 0.5:
            parts.append("air defense CAP fighter intercept protect bases")
        else:
            parts.append("air parity balanced CAP recon")

        if getattr(signals, "fighters_ready", 0) > 0:
            parts.append("fighter OCAP DCAP intercept patrol")
        if getattr(signals, "bombers_ready", 0) > 0:
            parts.append("bomber bombardment target selection")
        if getattr(signals, "unsighted_enemy_hexes", 0) > 3:
            parts.append("reconnaissance sighting enemy positions")
        if getattr(signals, "ground_support_needed", False):
            parts.append("close air support ground attack combat")
        if getattr(signals, "units_in_contact", 0) > 0:
            parts.append("tactical air support frontline")

    # Naval/fleet-specific mappings
    signals_phase = getattr(signals, "phase", "")
    if "convoy" in situation.lower() or "fleet" in situation.lower() or signals_phase == "fleet":
        if getattr(signals, "fleet_available", False):
            parts.append("fleet sortie convoy interception naval strike")
        if getattr(signals, "convoy_losses", 0) > 0:
            parts.append("convoy defense CAP protection losses")
        if getattr(signals, "convoy_tonnage_planned", 0) > 0:
            parts.append("convoy planning tonnage port delivery supply")
        if getattr(signals, "ports_held", 0) <= 1:
            parts.append("port capacity limited single port supply route")
        if getattr(signals, "nearest_port_distance", 999) > 20:
            parts.append("port distance supply line long haul truck")
        if getattr(signals, "supply_pool_fuel", 0) < 500:
            parts.append("fuel shortage convoy priority fuel delivery")
        if getattr(signals, "supply_pool_water", 0) < 500:
            parts.append("water shortage convoy priority water delivery")

    return " ".join(parts)


def build_general_query(side: str, phase: str, vp_summary: str = "") -> str:
    """
    Build a semantic query string for the General agent.

    Args:
        side: "allied" or "axis".
        phase: Phase type.
        vp_summary: VP trend description (e.g., "losing, need aggressive attack").

    Returns:
        Natural language query string.
    """
    parts = [f"{side} commander {phase}"]
    if vp_summary:
        parts.append(vp_summary)
    return " ".join(parts)
