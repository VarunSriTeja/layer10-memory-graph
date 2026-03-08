"""
Retrieval system for querying the memory graph
"""
import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path
import math

import numpy as np
from sentence_transformers import SentenceTransformer

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from config import EMBEDDING_MODEL, MAX_RESULTS, MAX_HOPS, TOP_K_PER_HOP
from src.database.models import Entity, Claim, Evidence
from src.graph.graph_builder import MemoryGraph


@dataclass
class ContextPack:
    """Result of a retrieval query"""
    query: str
    summary: str = ""
    entities: List[Entity] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    evidence_snippets: List[Evidence] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    confidence: float = 0.0
    ambiguities: List[Dict] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "summary": self.summary,
            "entities": [e.to_dict() for e in self.entities],
            "claims": [c.to_dict() for c in self.claims],
            "evidence_snippets": [e.to_dict() for e in self.evidence_snippets],
            "citations": self.citations,
            "confidence": self.confidence,
            "ambiguities": self.ambiguities,
            "metadata": self.metadata
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class Retriever:
    """Retrieves relevant context from the memory graph"""
    
    def __init__(self, graph: MemoryGraph, embedding_model: str = EMBEDDING_MODEL):
        self.graph = graph
        self.embedding_model = SentenceTransformer(embedding_model)
        
        # Claim type keywords for extraction
        self.claim_keywords = {
            "ASSIGNED_TO": ["assigned", "assignee", "working on", "responsible", "who"],
            "FIXED_BY": ["fixed", "fix", "resolved", "closed by", "merged"],
            "AFFECTS_COMPONENT": ["affects", "component", "area", "module", "bugs", "bug", "issues", "has"],
            "REPORTED_BY": ["reported", "created", "opened", "filed"],
            "DUPLICATES": ["duplicate", "same as", "dupe"],
            "DECISION": ["decision", "decided", "won't fix", "by design"],
            "BLOCKS": ["blocks", "blocking", "blocker"],
            "MENTIONS": ["mentioned", "mentioned by", "cc"],
            "HAS_LABEL": ["label", "tagged", "labeled"],
            "STATE": ["state", "status", "open", "closed"],
        }
        
        # Abstract query expansion
        self.query_expansion = {
            "bugs": ["AFFECTS_COMPONENT", "HAS_LABEL"],
            "issues": ["AFFECTS_COMPONENT", "REPORTED_BY"],
            "problems": ["AFFECTS_COMPONENT", "HAS_LABEL"],
            "components": ["AFFECTS_COMPONENT"],
            "people": ["ASSIGNED_TO", "REPORTED_BY", "MENTIONS"],
            "team": ["ASSIGNED_TO", "MENTIONS"],
            "show": [],  # generic, match all
            "list": [],  # generic, match all
        }
        
        # Component keywords
        self.component_keywords = {
            "terminal": ["terminal", "console", "shell", "command line"],
            "editor": ["editor", "text", "cursor", "selection"],
            "git": ["git", "source control", "scm", "version control"],
            "debugger": ["debug", "debugger", "breakpoint", "debugging"],
            "extensions": ["extension", "plugin", "marketplace"],
        }
    
    def _extract_query_intent(self, query: str) -> Dict:
        """Extract intent, entities, and filters from query"""
        query_lower = query.lower()
        
        intent = {
            "entity_types": [],
            "claim_types": [],
            "components": [],
            "time_filter": None,
            "keywords": [],
        }
        
        # Apply query expansion for abstract terms
        for term, claim_types in self.query_expansion.items():
            if term in query_lower:
                intent["claim_types"].extend(claim_types)
        
        # Extract claim type intent
        for claim_type, keywords in self.claim_keywords.items():
            if any(kw in query_lower for kw in keywords):
                intent["claim_types"].append(claim_type)
        
        # Deduplicate claim types
        intent["claim_types"] = list(set(intent["claim_types"]))
        
        # If still no claim types and query is abstract, default to common types
        if not intent["claim_types"]:
            intent["claim_types"] = ["AFFECTS_COMPONENT", "ASSIGNED_TO", "REPORTED_BY", "DECISION"]
        
        # Extract component intent
        for component, keywords in self.component_keywords.items():
            if any(kw in query_lower for kw in keywords):
                intent["components"].append(component)
        
        # Extract time filter
        time_patterns = [
            (r"last (\d+) days?", lambda m: timedelta(days=int(m.group(1)))),
            (r"last week", lambda m: timedelta(weeks=1)),
            (r"last month", lambda m: timedelta(days=30)),
            (r"last (\d+) months?", lambda m: timedelta(days=int(m.group(1)) * 30)),
            (r"recent", lambda m: timedelta(days=30)),
            (r"today", lambda m: timedelta(days=1)),
        ]
        
        for pattern, delta_fn in time_patterns:
            match = re.search(pattern, query_lower)
            if match:
                intent["time_filter"] = datetime.now(timezone.utc) - delta_fn(match)
                break
        
        # Extract issue numbers
        issue_refs = re.findall(r"#(\d+)", query)
        if issue_refs:
            intent["entity_types"].append(("Issue", issue_refs))
        
        # Extract @mentions
        mentions = re.findall(r"@(\w+)", query)
        if mentions:
            intent["entity_types"].append(("Person", mentions))
        
        # Extract general keywords
        stopwords = {"what", "who", "when", "where", "how", "is", "are", "the", "a", "an", 
                     "in", "on", "at", "to", "for", "of", "with", "by", "from", "was", "were"}
        words = re.findall(r'\b\w+\b', query_lower)
        intent["keywords"] = [w for w in words if w not in stopwords and len(w) > 2]
        
        return intent
    
    def _embed_text(self, text: str) -> np.ndarray:
        """Generate embedding for text"""
        return self.embedding_model.encode(text, convert_to_numpy=True)
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors"""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    
    def _recency_decay(self, timestamp, half_life_days: int = 180) -> float:
        """Compute recency decay score"""
        if not timestamp:
            return 0.5
        
        # Handle string timestamps from database
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return 0.5
        
        # Handle timezone-aware vs naive datetimes
        now = datetime.now()
        if timestamp.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=None)
        
        days_old = (now - timestamp).days
        return math.exp(-days_old / half_life_days)
    
    def _score_claim(
        self, 
        claim: Claim, 
        query_embedding: np.ndarray,
        intent: Dict
    ) -> float:
        """Score a claim for relevance to query"""
        score = 0.0
        
        # 1. Claim type match (0.25)
        if claim.claim_type in intent["claim_types"]:
            score += 0.25
        
        # 2. Confidence (0.25)
        score += 0.25 * claim.confidence
        
        # 3. Recency (0.20)
        if claim.validity_start:
            score += 0.20 * self._recency_decay(claim.validity_start)
        
        # 4. Evidence support (0.15)
        evidence_score = min(len(claim.evidence) / 3, 1.0)
        score += 0.15 * evidence_score
        
        # 5. Text similarity (0.15)
        claim_text = f"{claim.claim_type} {claim.subject_id} {claim.object_id or ''}"
        if claim.value:
            claim_text += f" {json.dumps(claim.value)}"
        claim_embedding = self._embed_text(claim_text)
        similarity = self._cosine_similarity(query_embedding, claim_embedding)
        score += 0.15 * max(0, similarity)
        
        return score
    
    def _keyword_search_entities(self, keywords: List[str], limit: int = 50) -> List[Entity]:
        """Search entities by keywords"""
        entities = []
        for keyword in keywords:
            results = self.graph.search_entities(keyword, limit=limit // len(keywords) if keywords else limit)
            entities.extend(results)
        
        # Deduplicate
        seen = set()
        unique = []
        for e in entities:
            if e.id not in seen:
                seen.add(e.id)
                unique.append(e)
        
        return unique[:limit]
    
    def _keyword_search_evidence(self, keywords: List[str], limit: int = 50) -> List[Evidence]:
        """Full-text search on evidence"""
        if not keywords:
            return []
        
        query = " OR ".join(keywords)
        return self.graph.search_evidence(query, limit=limit)
    
    def _expand_from_entities(
        self, 
        entity_ids: Set[str], 
        max_hops: int = MAX_HOPS,
        top_k: int = TOP_K_PER_HOP
    ) -> Set[str]:
        """Expand to find related entities via graph traversal"""
        all_entities = set(entity_ids)
        
        for _ in range(max_hops):
            new_entities = set()
            for entity_id in entity_ids:
                neighbors = self.graph.get_neighbors(entity_id, hops=1)
                new_entities.update(neighbors)
            
            # Limit expansion
            if len(new_entities) > top_k:
                # Just take first top_k for simplicity
                new_entities = set(list(new_entities)[:top_k])
            
            all_entities.update(new_entities)
            entity_ids = new_entities
        
        return all_entities
    
    def query(
        self, 
        query: str, 
        max_results: int = MAX_RESULTS,
        include_evidence: bool = True
    ) -> ContextPack:
        """
        Query the memory graph and return a context pack
        
        Args:
            query: Natural language question
            max_results: Maximum number of claims to return
            include_evidence: Whether to include evidence snippets
        
        Returns:
            ContextPack with relevant entities, claims, and evidence
        """
        start_time = datetime.now()
        
        # 1. Extract query intent
        intent = self._extract_query_intent(query)
        query_embedding = self._embed_text(query)
        
        # 2. Find seed entities
        seed_entities = set()
        
        # From explicit references
        for entity_type, refs in intent.get("entity_types", []):
            for ref in refs:
                if entity_type == "Issue":
                    seed_entities.add(f"issue:{ref}")
                elif entity_type == "Person":
                    seed_entities.add(f"person:{ref}")
        
        # From component keywords
        for component in intent.get("components", []):
            seed_entities.add(f"component:{component}")
        
        # From keyword search
        keyword_entities = self._keyword_search_entities(intent["keywords"], limit=20)
        seed_entities.update(e.id for e in keyword_entities)
        
        # 3. Expand via graph traversal
        expanded_entities = self._expand_from_entities(seed_entities, max_hops=MAX_HOPS)
        
        # 4. Collect claims for expanded entities
        all_claims = []
        for entity_id in expanded_entities:
            claims = self.graph.get_claims_for_entity(entity_id, current_only=True)
            
            # Apply time filter
            if intent["time_filter"]:
                time_filter = intent["time_filter"]
                filtered_claims = []
                for c in claims:
                    if c.validity_start:
                        # Handle both string and datetime validity_start
                        validity_start = c.validity_start
                        if isinstance(validity_start, str):
                            from datetime import datetime as dt
                            try:
                                validity_start = dt.fromisoformat(validity_start.replace("Z", "+00:00"))
                            except:
                                continue
                        if validity_start >= time_filter:
                            filtered_claims.append(c)
                claims = filtered_claims
            
            all_claims.extend(claims)
        
        # 4b. If no claims found via entities, try direct claim type search
        if not all_claims and intent["claim_types"]:
            for claim_type in intent["claim_types"]:
                type_claims = self.graph.get_claims_by_type(claim_type, limit=30)
                all_claims.extend(type_claims)
        
        # Deduplicate claims
        seen_claims = set()
        unique_claims = []
        for claim in all_claims:
            if claim.id not in seen_claims:
                seen_claims.add(claim.id)
                unique_claims.append(claim)
        
        # 5. Score and rank claims
        scored_claims = [
            (claim, self._score_claim(claim, query_embedding, intent))
            for claim in unique_claims
        ]
        scored_claims.sort(key=lambda x: x[1], reverse=True)
        
        # Take top results
        top_claims = [claim for claim, score in scored_claims[:max_results]]
        
        # 6. Collect entities from top claims
        result_entity_ids = set()
        for claim in top_claims:
            result_entity_ids.add(claim.subject_id)
            if claim.object_id:
                result_entity_ids.add(claim.object_id)
        
        result_entities = []
        for entity_id in result_entity_ids:
            entity = self.graph.get_entity(entity_id)
            if entity:
                result_entities.append(entity)
        
        # 7. Collect evidence
        evidence_snippets = []
        if include_evidence:
            for claim in top_claims:
                evidence_snippets.extend(claim.evidence[:3])  # Max 3 per claim
        
        # 8. Generate citations
        citations = []
        for i, ev in enumerate(evidence_snippets[:10]):
            citations.append(f"[{i+1}] {ev.source_url or ev.source_id}")
        
        # 9. Detect ambiguities/conflicts
        ambiguities = self._detect_conflicts(top_claims)
        
        # 10. Calculate overall confidence
        if scored_claims:
            avg_score = sum(s for _, s in scored_claims[:max_results]) / min(len(scored_claims), max_results)
        else:
            avg_score = 0.0
        
        # Build context pack
        context_pack = ContextPack(
            query=query,
            summary=self._generate_summary(query, top_claims, result_entities),
            entities=result_entities,
            claims=top_claims,
            evidence_snippets=evidence_snippets[:20],
            citations=citations,
            confidence=avg_score,
            ambiguities=ambiguities,
            metadata={
                "entities_searched": len(expanded_entities),
                "claims_evaluated": len(unique_claims),
                "retrieval_time_ms": (datetime.now() - start_time).total_seconds() * 1000,
                "intent": intent
            }
        )
        
        return context_pack
    
    def _detect_conflicts(self, claims: List[Claim]) -> List[Dict]:
        """Detect conflicting claims"""
        conflicts = []
        
        # Group claims by subject and type
        grouped = {}
        for claim in claims:
            key = (claim.subject_id, claim.claim_type)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(claim)
        
        # Find groups with multiple different values
        for (subject, ctype), group_claims in grouped.items():
            if len(group_claims) > 1:
                values = set()
                for c in group_claims:
                    val = c.object_id or json.dumps(c.value)
                    values.add(val)
                
                if len(values) > 1:
                    conflicts.append({
                        "subject": subject,
                        "claim_type": ctype,
                        "conflicting_values": list(values),
                        "claims": [c.id for c in group_claims]
                    })
        
        return conflicts
    
    def _generate_summary(
        self, 
        query: str, 
        claims: List[Claim], 
        entities: List[Entity]
    ) -> str:
        """Generate a brief summary of results"""
        if not claims:
            return "No relevant information found."
        
        entity_types = {}
        for e in entities:
            entity_types[e.type] = entity_types.get(e.type, 0) + 1
        
        claim_types = {}
        for c in claims:
            claim_types[c.claim_type] = claim_types.get(c.claim_type, 0) + 1
        
        parts = [f"Found {len(claims)} relevant claims"]
        
        if entity_types:
            type_strs = [f"{count} {etype}(s)" for etype, count in entity_types.items()]
            parts.append(f"involving {', '.join(type_strs)}")
        
        return " ".join(parts) + "."


def main():
    """Test retrieval"""
    from src.graph import MemoryGraph
    
    graph = MemoryGraph()
    graph.build_networkx_graph()
    
    retriever = Retriever(graph)
    
    # Test query
    result = retriever.query("What terminal bugs were fixed recently?")
    
    print("=== Context Pack ===")
    print(f"Query: {result.query}")
    print(f"Summary: {result.summary}")
    print(f"Entities: {len(result.entities)}")
    print(f"Claims: {len(result.claims)}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Metadata: {result.metadata}")
    
    graph.close()


if __name__ == "__main__":
    main()
