"""
Deduplication and canonicalization for entities, claims, and evidence
"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import List, Dict, Set, Tuple, Optional
from collections import defaultdict
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from config import SIMILARITY_THRESHOLD, HASH_ALGORITHM
from src.database.models import Entity, Claim, Evidence, MergeRecord, Alias


class Deduplicator:
    """Handles deduplication at artifact, entity, and claim levels"""
    
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn  # Optional database connection for persistence
        self.entity_index: Dict[str, Entity] = {}  # id -> entity
        self.alias_index: Dict[str, str] = {}  # alias -> canonical_id
        self.claim_signatures: Dict[str, str] = {}  # signature -> claim_id
        self.content_hashes: Dict[str, int] = {}  # hash -> evidence_id
        self.merge_history: List[MergeRecord] = []
        
        # Load existing data from database if connection provided
        if self.conn:
            self._load_from_database()
    
    def _load_from_database(self):
        """Load existing aliases and content hashes from database"""
        cursor = self.conn.cursor()
        
        # Load aliases
        try:
            cursor.execute("SELECT alias_value, entity_id FROM aliases")
            for row in cursor.fetchall():
                self.alias_index[row["alias_value"].lower()] = row["entity_id"]
        except Exception as e:
            pass  # Table might be empty after reset
        
        # Load content hashes
        try:
            cursor.execute("SELECT content_hash, id FROM evidence WHERE content_hash IS NOT NULL")
            for row in cursor.fetchall():
                self.content_hashes[row["content_hash"]] = row["id"]
        except Exception as e:
            pass  # Table might be empty after reset
    
    # ==================== Text Normalization ====================
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for comparison"""
        if not text:
            return ""
        # Lowercase
        text = text.lower()
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Remove markdown formatting
        text = re.sub(r'[*_`~]', '', text)
        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)
        # Remove @mentions (keep for entity extraction separately)
        text = re.sub(r'@\w+', '', text)
        return text
    
    @staticmethod
    def compute_hash(text: str) -> str:
        """Compute content hash for deduplication"""
        normalized = Deduplicator.normalize_text(text)
        return hashlib.sha256(normalized.encode()).hexdigest()
    
    @staticmethod
    def compute_claim_signature(claim: Claim) -> str:
        """Compute unique signature for a claim to detect duplicates"""
        components = [
            claim.claim_type,
            claim.subject_id,
            claim.object_id or "",
            json.dumps(claim.value, sort_keys=True) if claim.value else ""
        ]
        signature_str = "|".join(components)
        return hashlib.sha256(signature_str.encode()).hexdigest()[:16]
    
    # ==================== Entity Deduplication ====================
    
    def canonicalize_person_id(self, identifier: str) -> str:
        """
        Convert various person identifiers to canonical form
        - GitHub username: person:username
        - Email: person:username (if known mapping)
        """
        identifier = identifier.lower().strip()
        
        # Remove @ prefix if present
        if identifier.startswith("@"):
            identifier = identifier[1:]
        
        # Check alias index
        if identifier in self.alias_index:
            return self.alias_index[identifier]
        
        return f"person:{identifier}"
    
    def canonicalize_component_id(self, name: str) -> str:
        """Convert component name to canonical form"""
        name = name.lower().strip()
        
        # Common aliases
        component_aliases = {
            "integrated terminal": "terminal",
            "terminal panel": "terminal",
            "text editor": "editor",
            "editor-core": "editor",
            "source control": "git",
            "scm": "git",
            "version control": "git",
            "debug": "debugger",
            "debugging": "debugger",
            "extension host": "extensions",
            "extension-host": "extensions",
        }
        
        for alias, canonical in component_aliases.items():
            if alias in name:
                return f"component:{canonical}"
        
        # Default: use name as-is
        return f"component:{name}"
    
    def register_entity(self, entity: Entity) -> Entity:
        """
        Register entity and return canonical version
        Handles deduplication by ID and aliases
        """
        canonical_id = entity.id
        
        # Canonicalize based on type
        if entity.type == "Person":
            canonical_id = self.canonicalize_person_id(entity.id.replace("person:", ""))
        elif entity.type == "Component":
            canonical_id = self.canonicalize_component_id(entity.id.replace("component:", ""))
        
        # Check if already registered
        if canonical_id in self.entity_index:
            existing = self.entity_index[canonical_id]
            # Merge properties
            merged_props = {**existing.properties, **entity.properties}
            existing.properties = merged_props
            existing.updated_at = datetime.now()
            return existing
        
        # Register new entity
        entity.id = canonical_id
        self.entity_index[canonical_id] = entity
        
        # Register aliases
        self.register_alias(canonical_id, entity.canonical_name)
        
        return entity
    
    def register_alias(self, entity_id: str, alias: str, alias_type: str = "canonical"):
        """Register an alias for an entity (in memory - persisted later)"""
        alias_lower = alias.lower().strip()
        if alias_lower and alias_lower not in self.alias_index:
            self.alias_index[alias_lower] = entity_id
    
    def persist_all_aliases(self):
        """Persist all collected aliases to database (call after entities are in DB)"""
        if not self.conn:
            return 0
        
        persisted = 0
        cursor = self.conn.cursor()
        for alias_value, entity_id in self.alias_index.items():
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO aliases (entity_id, alias_value, alias_type)
                    VALUES (?, ?, ?)
                """, (entity_id, alias_value, "canonical"))
                if cursor.rowcount > 0:
                    persisted += 1
            except Exception:
                pass  # Skip aliases for missing entities
        self.conn.commit()
        return persisted
    
    def persist_all_merge_history(self):
        """Persist all collected merge records to database"""
        if not self.conn:
            return 0
        
        persisted = 0
        for record in self.merge_history:
            try:
                self._persist_merge_record(record)
                persisted += 1
            except Exception:
                pass
        return persisted
    
    def merge_entities(
        self, 
        source_ids: List[str], 
        target_id: str, 
        reason: str,
        confidence: float = 1.0,
        automated: bool = True
    ) -> MergeRecord:
        """
        Merge multiple entities into one
        Maintains history for reversibility
        """
        # Capture pre-merge state
        pre_merge = {
            "entities": {sid: self.entity_index.get(sid) for sid in source_ids if sid in self.entity_index}
        }
        
        # Get target entity
        target = self.entity_index.get(target_id)
        if not target:
            raise ValueError(f"Target entity {target_id} not found")
        
        # Merge properties and aliases from sources
        for source_id in source_ids:
            source = self.entity_index.get(source_id)
            if source and source_id != target_id:
                # Merge properties
                target.properties = {**source.properties, **target.properties}
                
                # Redirect aliases
                for alias, eid in list(self.alias_index.items()):
                    if eid == source_id:
                        self.alias_index[alias] = target_id
                
                # Remove source entity
                del self.entity_index[source_id]
        
        # Record merge
        merge_record = MergeRecord(
            merge_type="entity",
            source_ids=source_ids,
            target_id=target_id,
            reason=reason,
            confidence=confidence,
            automated=automated,
            pre_merge_snapshot=pre_merge,
            created_at=datetime.now()
        )
        self.merge_history.append(merge_record)
        
        # Persist to database
        if self.conn:
            self._persist_merge_record(merge_record)
        
        return merge_record
    
    def _persist_merge_record(self, record: MergeRecord):
        """Persist merge record to database"""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO merge_history 
                (merge_type, source_ids, target_id, reason, confidence, automated, pre_merge_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                record.merge_type,
                json.dumps(record.source_ids),
                record.target_id,
                record.reason,
                record.confidence,
                1 if record.automated else 0,
                json.dumps(record.pre_merge_snapshot, default=str) if record.pre_merge_snapshot else None
            ))
            self.conn.commit()
        except Exception as e:
            print(f"Warning: Could not persist merge record: {e}")
    
    def find_duplicate_entities(self, entities: List[Entity]) -> List[Tuple[str, str, float]]:
        """
        Find potential duplicate entities
        Returns list of (id1, id2, similarity_score) tuples
        """
        duplicates = []
        
        # Group by type
        by_type: Dict[str, List[Entity]] = defaultdict(list)
        for entity in entities:
            by_type[entity.type].append(entity)
        
        # Check within each type
        for entity_type, type_entities in by_type.items():
            for i, e1 in enumerate(type_entities):
                for e2 in type_entities[i+1:]:
                    similarity = self._entity_similarity(e1, e2)
                    if similarity >= SIMILARITY_THRESHOLD:
                        duplicates.append((e1.id, e2.id, similarity))
        
        return duplicates
    
    def _entity_similarity(self, e1: Entity, e2: Entity) -> float:
        """Compute similarity between two entities"""
        if e1.type != e2.type:
            return 0.0
        
        # Same ID = definitely same
        if e1.id == e2.id:
            return 1.0
        
        # For people: check GitHub user ID
        if e1.type == "Person":
            gh_id1 = e1.properties.get("github_id")
            gh_id2 = e2.properties.get("github_id")
            if gh_id1 and gh_id2 and gh_id1 == gh_id2:
                return 1.0
        
        # Name similarity
        name1 = e1.canonical_name.lower()
        name2 = e2.canonical_name.lower()
        
        if name1 == name2:
            return 0.95
        
        # Simple substring check
        if name1 in name2 or name2 in name1:
            return 0.8
        
        # Levenshtein-ish check (simplified)
        common_chars = len(set(name1) & set(name2))
        total_chars = len(set(name1) | set(name2))
        if total_chars > 0:
            return common_chars / total_chars * 0.7
        
        return 0.0
    
    # ==================== Claim Deduplication ====================
    
    def deduplicate_claims(self, claims: List[Claim]) -> List[Claim]:
        """
        Deduplicate claims by signature
        Merges evidence from duplicate claims
        """
        signature_to_claim: Dict[str, Claim] = {}
        
        for claim in claims:
            signature = self.compute_claim_signature(claim)
            
            if signature in signature_to_claim:
                # Merge evidence
                existing = signature_to_claim[signature]
                existing.evidence.extend(claim.evidence)
                # Update confidence (boost with more evidence)
                existing.confidence = min(1.0, existing.confidence + 0.05)
                # Keep earliest validity_start
                if claim.validity_start and (
                    not existing.validity_start or 
                    claim.validity_start < existing.validity_start
                ):
                    existing.validity_start = claim.validity_start
            else:
                signature_to_claim[signature] = claim
                self.claim_signatures[signature] = claim.id
        
        return list(signature_to_claim.values())
    
    # ==================== Evidence Deduplication ====================
    
    def deduplicate_evidence(self, evidences: List[Evidence]) -> List[Evidence]:
        """
        Deduplicate evidence by content hash
        """
        unique_evidences = []
        
        for evidence in evidences:
            content = evidence.full_content or evidence.excerpt or ""
            content_hash = self.compute_hash(content)
            
            if content_hash not in self.content_hashes:
                evidence.content_hash = content_hash
                unique_evidences.append(evidence)
                self.content_hashes[content_hash] = evidence.id or len(unique_evidences)
        
        return unique_evidences
    
    def is_quoted_content(self, text: str) -> bool:
        """Check if text is primarily quoted content (email/GitHub style)"""
        if not text:
            return False
        
        lines = text.strip().split('\n')
        quoted_lines = sum(1 for line in lines if line.strip().startswith('>'))
        
        return quoted_lines > len(lines) * 0.5
    
    # ==================== Full Pipeline ====================
    
    def process_extraction(
        self, 
        entities: List[Entity], 
        claims: List[Claim], 
        evidences: List[Evidence]
    ) -> Tuple[List[Entity], List[Claim], List[Evidence]]:
        """
        Full deduplication pipeline
        
        Args:
            entities: Raw extracted entities
            claims: Raw extracted claims
            evidences: Raw extracted evidences
        
        Returns:
            Deduplicated and canonicalized (entities, claims, evidences)
        """
        # 1. Deduplicate evidence first
        unique_evidences = self.deduplicate_evidence(evidences)
        
        # 2. Register and canonicalize entities
        canonical_entities = []
        for entity in entities:
            canonical = self.register_entity(entity)
            if canonical.id not in [e.id for e in canonical_entities]:
                canonical_entities.append(canonical)
        
        # 3. Update claim references to canonical IDs
        for claim in claims:
            # Update subject_id
            if claim.subject_id.startswith("person:"):
                claim.subject_id = self.canonicalize_person_id(
                    claim.subject_id.replace("person:", "")
                )
            elif claim.subject_id.startswith("component:"):
                claim.subject_id = self.canonicalize_component_id(
                    claim.subject_id.replace("component:", "")
                )
            
            # Update object_id if present
            if claim.object_id:
                if claim.object_id.startswith("person:"):
                    claim.object_id = self.canonicalize_person_id(
                        claim.object_id.replace("person:", "")
                    )
                elif claim.object_id.startswith("component:"):
                    claim.object_id = self.canonicalize_component_id(
                        claim.object_id.replace("component:", "")
                    )
        
        # 4. Deduplicate claims
        unique_claims = self.deduplicate_claims(claims)
        
        # 5. Find and flag potential duplicate entities
        duplicates = self.find_duplicate_entities(canonical_entities)
        for id1, id2, score in duplicates:
            print(f"Potential duplicate: {id1} <-> {id2} (similarity: {score:.2f})")
        
        return canonical_entities, unique_claims, unique_evidences
    
    def get_statistics(self) -> dict:
        """Get deduplication statistics"""
        return {
            "total_entities": len(self.entity_index),
            "total_aliases": len(self.alias_index),
            "total_claim_signatures": len(self.claim_signatures),
            "total_content_hashes": len(self.content_hashes),
            "merge_operations": len(self.merge_history)
        }


def main():
    """Test deduplication"""
    dedup = Deduplicator()
    
    # Test entity canonicalization
    e1 = Entity(id="person:TestUser", type="Person", canonical_name="Test User")
    e2 = Entity(id="person:testuser", type="Person", canonical_name="TestUser")
    
    c1 = dedup.register_entity(e1)
    c2 = dedup.register_entity(e2)
    
    print(f"Entity 1 canonical ID: {c1.id}")
    print(f"Entity 2 canonical ID: {c2.id}")
    print(f"Same entity? {c1.id == c2.id}")
    
    print(f"\nStatistics: {dedup.get_statistics()}")


if __name__ == "__main__":
    main()
