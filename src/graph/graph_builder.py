"""
Memory Graph Builder - Constructs and maintains the knowledge graph
"""
import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path

import networkx as nx

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from config import DB_PATH
from src.database.schema import get_connection, init_database
from src.database.models import Entity, Claim, Evidence


class MemoryGraph:
    """
    Memory graph that combines SQLite storage with NetworkX for traversal
    """
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = None
        self.graph = nx.DiGraph()
        self._ensure_db()
    
    def _ensure_db(self):
        """Ensure database exists and is initialized"""
        if not self.db_path.exists():
            init_database(self.db_path)
        self.conn = get_connection(self.db_path)
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
    
    # ==================== Entity Operations ====================
    
    def add_entity(self, entity: Entity) -> bool:
        """
        Add or update entity in database
        Returns True if inserted, False if updated
        """
        cursor = self.conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO entities (id, type, canonical_name, properties, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                entity.id,
                entity.type,
                entity.canonical_name,
                json.dumps(entity.properties),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            self.conn.commit()
            
            # Add to NetworkX graph
            self.graph.add_node(entity.id, **entity.to_dict())
            return True
            
        except sqlite3.IntegrityError:
            # Entity exists, update it
            cursor.execute("""
                UPDATE entities 
                SET canonical_name = ?, properties = ?, updated_at = ?
                WHERE id = ?
            """, (
                entity.canonical_name,
                json.dumps(entity.properties),
                datetime.now().isoformat(),
                entity.id
            ))
            self.conn.commit()
            
            # Update NetworkX graph
            if entity.id in self.graph:
                self.graph.nodes[entity.id].update(entity.to_dict())
            else:
                self.graph.add_node(entity.id, **entity.to_dict())
            return False
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get entity by ID"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        row = cursor.fetchone()
        
        if row:
            return Entity.from_row(row)
        return None
    
    def get_entities_by_type(self, entity_type: str) -> List[Entity]:
        """Get all entities of a specific type"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM entities WHERE type = ? AND deleted_at IS NULL", (entity_type,))
        return [Entity.from_row(row) for row in cursor.fetchall()]
    
    def search_entities(self, query: str, entity_type: Optional[str] = None, limit: int = 20) -> List[Entity]:
        """Search entities by name"""
        cursor = self.conn.cursor()
        
        sql = "SELECT * FROM entities WHERE canonical_name LIKE ? AND deleted_at IS NULL"
        params = [f"%{query}%"]
        
        if entity_type:
            sql += " AND type = ?"
            params.append(entity_type)
        
        sql += " LIMIT ?"
        params.append(limit)
        
        cursor.execute(sql, params)
        return [Entity.from_row(row) for row in cursor.fetchall()]
    
    # ==================== Alias Operations ====================
    
    def add_alias(self, entity_id: str, alias_value: str, alias_type: str = None):
        """Add alias for an entity"""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO aliases (entity_id, alias_value, alias_type, created_at)
                VALUES (?, ?, ?, ?)
            """, (entity_id, alias_value, alias_type, datetime.now().isoformat()))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass  # Alias already exists
    
    def resolve_alias(self, alias: str) -> Optional[str]:
        """Resolve alias to entity ID"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT entity_id FROM aliases WHERE alias_value = ?", (alias.lower(),))
        row = cursor.fetchone()
        return row["entity_id"] if row else None
    
    # ==================== Evidence Operations ====================
    
    def add_evidence(self, evidence: Evidence) -> int:
        """Add evidence to database, returns evidence ID"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO evidence (
                    source_type, source_id, source_url, excerpt, full_content,
                    char_start, char_end, timestamp, author_id, raw_data, 
                    content_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                evidence.source_type,
                evidence.source_id,
                evidence.source_url,
                evidence.excerpt,
                evidence.full_content,
                evidence.char_start,
                evidence.char_end,
                evidence.timestamp.isoformat() if evidence.timestamp else None,
                evidence.author_id,
                json.dumps(evidence.raw_data) if evidence.raw_data else None,
                evidence.content_hash,
                datetime.now().isoformat()
            ))
            self.conn.commit()
            return cursor.lastrowid
            
        except sqlite3.IntegrityError:
            # Evidence already exists
            cursor.execute(
                "SELECT id FROM evidence WHERE source_type = ? AND source_id = ?",
                (evidence.source_type, evidence.source_id)
            )
            row = cursor.fetchone()
            return row["id"] if row else -1
    
    def get_evidence(self, evidence_id: int) -> Optional[Evidence]:
        """Get evidence by ID"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
        row = cursor.fetchone()
        return Evidence.from_row(row) if row else None
    
    def search_evidence(self, query: str, limit: int = 20) -> List[Evidence]:
        """Full-text search on evidence excerpts"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT e.* FROM evidence e
            JOIN evidence_fts fts ON e.id = fts.rowid
            WHERE evidence_fts MATCH ?
            LIMIT ?
        """, (query, limit))
        return [Evidence.from_row(row) for row in cursor.fetchall()]
    
    # ==================== Claim Operations ====================
    
    def add_claim(self, claim: Claim, evidence_ids: List[int] = None) -> bool:
        """
        Add claim to database
        Returns True if inserted, False if updated
        """
        cursor = self.conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO claims (
                    id, claim_type, subject_id, object_id, value, confidence,
                    validity_start, validity_end, version, superseded_by,
                    extraction_version, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                claim.id,
                claim.claim_type,
                claim.subject_id,
                claim.object_id,
                json.dumps(claim.value) if claim.value else None,
                claim.confidence,
                claim.validity_start.isoformat() if claim.validity_start else None,
                claim.validity_end.isoformat() if claim.validity_end else None,
                claim.version,
                claim.superseded_by,
                claim.extraction_version,
                datetime.now().isoformat()
            ))
            
            # Link evidence to claim
            if evidence_ids:
                for ev_id in evidence_ids:
                    cursor.execute(
                        "INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id) VALUES (?, ?)",
                        (claim.id, ev_id)
                    )
            
            self.conn.commit()
            
            # Add edge to NetworkX graph
            if claim.object_id:
                self.graph.add_edge(
                    claim.subject_id, 
                    claim.object_id,
                    claim_id=claim.id,
                    claim_type=claim.claim_type,
                    confidence=claim.confidence
                )
            
            return True
            
        except sqlite3.IntegrityError:
            return False
    
    def get_claim(self, claim_id: str) -> Optional[Claim]:
        """Get claim by ID with evidence"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM claims WHERE id = ?", (claim_id,))
        row = cursor.fetchone()
        
        if not row:
            return None
        
        claim = Claim.from_row(row)
        
        # Load evidence
        cursor.execute("""
            SELECT e.* FROM evidence e
            JOIN claim_evidence ce ON e.id = ce.evidence_id
            WHERE ce.claim_id = ?
        """, (claim_id,))
        claim.evidence = [Evidence.from_row(r) for r in cursor.fetchall()]
        
        return claim
    
    def get_claims_by_type(self, claim_type: str, limit: int = 50) -> List[Claim]:
        """Get claims by claim type"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM claims WHERE claim_type = ? LIMIT ?",
            (claim_type, limit)
        )
        
        claims = []
        for row in cursor.fetchall():
            claim = Claim.from_row(row)
            # Load evidence
            cursor.execute("""
                SELECT e.* FROM evidence e
                JOIN claim_evidence ce ON e.id = ce.evidence_id
                WHERE ce.claim_id = ?
            """, (claim.id,))
            claim.evidence = [Evidence.from_row(r) for r in cursor.fetchall()]
            claims.append(claim)
        
        return claims
    
    def get_claims_for_entity(
        self, 
        entity_id: str, 
        claim_type: Optional[str] = None,
        current_only: bool = True
    ) -> List[Claim]:
        """Get all claims where entity is subject or object"""
        cursor = self.conn.cursor()
        
        sql = """
            SELECT * FROM claims 
            WHERE (subject_id = ? OR object_id = ?)
        """
        params = [entity_id, entity_id]
        
        if claim_type:
            sql += " AND claim_type = ?"
            params.append(claim_type)
        
        if current_only:
            sql += " AND validity_end IS NULL"
        
        cursor.execute(sql, params)
        claims = []
        
        for row in cursor.fetchall():
            claim = Claim.from_row(row)
            # Load evidence
            cursor.execute("""
                SELECT e.* FROM evidence e
                JOIN claim_evidence ce ON e.id = ce.evidence_id
                WHERE ce.claim_id = ?
            """, (claim.id,))
            claim.evidence = [Evidence.from_row(r) for r in cursor.fetchall()]
            claims.append(claim)
        
        return claims
    
    def supersede_claim(self, old_claim_id: str, new_claim: Claim):
        """Mark old claim as superseded by new claim"""
        cursor = self.conn.cursor()
        
        # Update old claim
        cursor.execute("""
            UPDATE claims 
            SET validity_end = ?, superseded_by = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), new_claim.id, old_claim_id))
        
        # Add new claim
        self.add_claim(new_claim)
        
        self.conn.commit()
    
    # ==================== Graph Operations ====================
    
    def build_networkx_graph(self, current_only: bool = True) -> nx.DiGraph:
        """Build NetworkX graph from database"""
        self.graph = nx.DiGraph()
        cursor = self.conn.cursor()
        
        # Add nodes (entities)
        cursor.execute("SELECT * FROM entities WHERE deleted_at IS NULL")
        for row in cursor.fetchall():
            entity = Entity.from_row(row)
            self.graph.add_node(entity.id, **entity.to_dict())
        
        # Add edges (claims)
        sql = "SELECT * FROM claims WHERE object_id IS NOT NULL"
        if current_only:
            sql += " AND validity_end IS NULL"
        
        cursor.execute(sql)
        for row in cursor.fetchall():
            claim = Claim.from_row(row)
            if self.graph.has_node(claim.subject_id) and self.graph.has_node(claim.object_id):
                self.graph.add_edge(
                    claim.subject_id,
                    claim.object_id,
                    claim_id=claim.id,
                    claim_type=claim.claim_type,
                    confidence=claim.confidence
                )
        
        return self.graph
    
    def get_neighbors(self, entity_id: str, hops: int = 1) -> Set[str]:
        """Get neighboring entity IDs within N hops"""
        if entity_id not in self.graph:
            return set()
        
        neighbors = set()
        current_level = {entity_id}
        
        for _ in range(hops):
            next_level = set()
            for node in current_level:
                next_level.update(self.graph.predecessors(node))
                next_level.update(self.graph.successors(node))
            neighbors.update(next_level)
            current_level = next_level - neighbors
        
        neighbors.discard(entity_id)
        return neighbors
    
    def get_subgraph(self, entity_ids: Set[str]) -> nx.DiGraph:
        """Get subgraph containing only specified entities"""
        return self.graph.subgraph(entity_ids).copy()
    
    # ==================== Statistics ====================
    
    def get_statistics(self) -> Dict:
        """Get graph statistics"""
        cursor = self.conn.cursor()
        
        cursor.execute("SELECT COUNT(*) as cnt FROM entities WHERE deleted_at IS NULL")
        entity_count = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM claims WHERE validity_end IS NULL")
        claim_count = cursor.fetchone()["cnt"]
        
        cursor.execute("SELECT COUNT(*) as cnt FROM evidence")
        evidence_count = cursor.fetchone()["cnt"]
        
        cursor.execute("""
            SELECT type, COUNT(*) as cnt FROM entities 
            WHERE deleted_at IS NULL 
            GROUP BY type
        """)
        entity_by_type = {row["type"]: row["cnt"] for row in cursor.fetchall()}
        
        cursor.execute("""
            SELECT claim_type, COUNT(*) as cnt FROM claims 
            WHERE validity_end IS NULL 
            GROUP BY claim_type
        """)
        claims_by_type = {row["claim_type"]: row["cnt"] for row in cursor.fetchall()}
        
        return {
            "total_entities": entity_count,
            "total_claims": claim_count,
            "total_evidence": evidence_count,
            "entities_by_type": entity_by_type,
            "claims_by_type": claims_by_type,
            "graph_nodes": self.graph.number_of_nodes(),
            "graph_edges": self.graph.number_of_edges()
        }
    
    # ==================== Export ====================
    
    def export_to_json(self, output_path: Path) -> Path:
        """Export graph to JSON format"""
        cursor = self.conn.cursor()
        
        # Export entities
        cursor.execute("SELECT * FROM entities WHERE deleted_at IS NULL")
        entities = [Entity.from_row(row).to_dict() for row in cursor.fetchall()]
        
        # Export claims with evidence
        cursor.execute("SELECT * FROM claims WHERE validity_end IS NULL")
        claims = []
        for row in cursor.fetchall():
            claim = Claim.from_row(row)
            cursor.execute("""
                SELECT e.* FROM evidence e
                JOIN claim_evidence ce ON e.id = ce.evidence_id
                WHERE ce.claim_id = ?
            """, (claim.id,))
            claim.evidence = [Evidence.from_row(r) for r in cursor.fetchall()]
            claims.append(claim.to_dict())
        
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "statistics": self.get_statistics(),
            "entities": entities,
            "claims": claims
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)
        
        return output_path


def main():
    """Test graph operations"""
    graph = MemoryGraph()
    
    # Add test data
    entity1 = Entity(id="person:test", type="Person", canonical_name="Test User")
    entity2 = Entity(id="issue:123", type="Issue", canonical_name="Test Issue")
    
    graph.add_entity(entity1)
    graph.add_entity(entity2)
    
    evidence = Evidence(
        source_type="test",
        source_id="test:123",
        excerpt="Test excerpt"
    )
    ev_id = graph.add_evidence(evidence)
    
    claim = Claim(
        id="claim:test-reported-123",
        claim_type="REPORTED_BY",
        subject_id="issue:123",
        object_id="person:test",
        confidence=1.0
    )
    graph.add_claim(claim, [ev_id])
    
    # Build graph and print stats
    graph.build_networkx_graph()
    print(f"Statistics: {graph.get_statistics()}")
    
    graph.close()


if __name__ == "__main__":
    main()
