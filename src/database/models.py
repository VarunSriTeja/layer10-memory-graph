"""
Data models for Layer10 Memory Graph
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
import json


@dataclass
class Entity:
    """Represents a node in the memory graph"""
    id: str  # e.g., "person:bpasero", "issue:12345"
    type: str  # 'Person', 'Issue', 'PullRequest', 'Component'
    canonical_name: str
    properties: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    embedding: Optional[bytes] = None
    
    @classmethod
    def from_row(cls, row) -> "Entity":
        """Create Entity from database row"""
        return cls(
            id=row["id"],
            type=row["type"],
            canonical_name=row["canonical_name"],
            properties=json.loads(row["properties"]) if row["properties"] else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row["deleted_at"],
            embedding=row["embedding"]
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "type": self.type,
            "canonical_name": self.canonical_name,
            "properties": self.properties
        }


@dataclass
class Claim:
    """Represents a fact/relationship with evidence"""
    id: str  # e.g., "claim:issue123-assigned-to-bpasero"
    claim_type: str  # 'ASSIGNED_TO', 'FIXED_BY', etc.
    subject_id: str
    object_id: Optional[str] = None
    value: Optional[dict] = None
    confidence: float = 1.0
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None
    version: int = 1
    superseded_by: Optional[str] = None
    extraction_version: Optional[str] = None
    created_at: Optional[datetime] = None
    evidence: List["Evidence"] = field(default_factory=list)
    embedding: Optional[bytes] = None
    
    @classmethod
    def from_row(cls, row) -> "Claim":
        """Create Claim from database row"""
        return cls(
            id=row["id"],
            claim_type=row["claim_type"],
            subject_id=row["subject_id"],
            object_id=row["object_id"],
            value=json.loads(row["value"]) if row["value"] else None,
            confidence=row["confidence"],
            validity_start=row["validity_start"],
            validity_end=row["validity_end"],
            version=row["version"],
            superseded_by=row["superseded_by"],
            extraction_version=row["extraction_version"],
            created_at=row["created_at"],
            embedding=row["embedding"]
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "claim_type": self.claim_type,
            "subject_id": self.subject_id,
            "object_id": self.object_id,
            "value": self.value,
            "confidence": self.confidence,
            "validity_start": str(self.validity_start) if self.validity_start else None,
            "validity_end": str(self.validity_end) if self.validity_end else None,
            "version": self.version,
            "evidence": [e.to_dict() for e in self.evidence]
        }
    
    @property
    def is_current(self) -> bool:
        """Check if claim is currently valid"""
        return self.validity_end is None


@dataclass
class Evidence:
    """Supporting evidence for a claim"""
    id: Optional[int] = None
    source_type: str = ""  # 'issue_body', 'comment', 'event', 'label'
    source_id: str = ""  # External ID
    source_url: Optional[str] = None
    excerpt: Optional[str] = None
    full_content: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    timestamp: Optional[datetime] = None
    author_id: Optional[str] = None
    raw_data: Optional[dict] = None
    content_hash: Optional[str] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> "Evidence":
        """Create Evidence from database row"""
        return cls(
            id=row["id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            source_url=row["source_url"],
            excerpt=row["excerpt"],
            full_content=row["full_content"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            timestamp=row["timestamp"],
            author_id=row["author_id"],
            raw_data=json.loads(row["raw_data"]) if row["raw_data"] else None,
            content_hash=row["content_hash"],
            created_at=row["created_at"]
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "excerpt": self.excerpt,
            "timestamp": str(self.timestamp) if self.timestamp else None,
            "author_id": self.author_id
        }


@dataclass
class Alias:
    """Alternative name for an entity"""
    id: Optional[int] = None
    entity_id: str = ""
    alias_value: str = ""
    alias_type: Optional[str] = None
    source_evidence_id: Optional[int] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> "Alias":
        return cls(
            id=row["id"],
            entity_id=row["entity_id"],
            alias_value=row["alias_value"],
            alias_type=row["alias_type"],
            source_evidence_id=row["source_evidence_id"],
            created_at=row["created_at"]
        )


@dataclass
class MergeRecord:
    """Record of entity/claim merge for reversibility"""
    id: Optional[int] = None
    merge_type: str = ""  # 'entity', 'claim'
    source_ids: List[str] = field(default_factory=list)
    target_id: str = ""
    reason: Optional[str] = None
    confidence: Optional[float] = None
    automated: bool = True
    pre_merge_snapshot: Optional[dict] = None
    created_at: Optional[datetime] = None
    reversed_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> "MergeRecord":
        return cls(
            id=row["id"],
            merge_type=row["merge_type"],
            source_ids=json.loads(row["source_ids"]) if row["source_ids"] else [],
            target_id=row["target_id"],
            reason=row["reason"],
            confidence=row["confidence"],
            automated=bool(row["automated"]),
            pre_merge_snapshot=json.loads(row["pre_merge_snapshot"]) if row["pre_merge_snapshot"] else None,
            created_at=row["created_at"],
            reversed_at=row["reversed_at"]
        )
