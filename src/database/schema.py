"""
SQLite database schema for Layer10 Memory Graph
"""
import sqlite3
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent.parent))
from config import DB_PATH


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get database connection with row factory"""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Initialize database with schema"""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # Enable FTS5 for full-text search
    cursor.executescript("""
        -- Entities table: People, Issues, PRs, Components
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,  -- 'Person', 'Issue', 'PullRequest', 'Component'
            canonical_name TEXT NOT NULL,
            properties TEXT,  -- JSON blob for type-specific attributes
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at TIMESTAMP,  -- Soft delete
            embedding BLOB  -- Vector embedding for similarity search
        );
        
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
        
        -- Aliases table: Alternative names for entities
        CREATE TABLE IF NOT EXISTS aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL REFERENCES entities(id),
            alias_value TEXT NOT NULL,
            alias_type TEXT,  -- 'username', 'display_name', 'email', 'label'
            source_evidence_id INTEGER REFERENCES evidence(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_id, alias_value)
        );
        
        CREATE INDEX IF NOT EXISTS idx_aliases_value ON aliases(alias_value);
        CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_id);
        
        -- Claims table: Relationships and facts with temporal validity
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            claim_type TEXT NOT NULL,  -- 'ASSIGNED_TO', 'FIXED_BY', 'AFFECTS_COMPONENT', etc.
            subject_id TEXT NOT NULL REFERENCES entities(id),
            object_id TEXT REFERENCES entities(id),  -- Nullable for non-entity claims
            value TEXT,  -- JSON for claim-specific data
            confidence REAL DEFAULT 1.0,
            validity_start TIMESTAMP,
            validity_end TIMESTAMP,  -- NULL means currently valid
            version INTEGER DEFAULT 1,
            superseded_by TEXT REFERENCES claims(id),
            extraction_version TEXT,  -- Track schema/model version
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding BLOB
        );
        
        CREATE INDEX IF NOT EXISTS idx_claims_type ON claims(claim_type);
        CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id);
        CREATE INDEX IF NOT EXISTS idx_claims_object ON claims(object_id);
        CREATE INDEX IF NOT EXISTS idx_claims_validity ON claims(validity_start, validity_end);
        
        -- Evidence table: Source material supporting claims
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,  -- 'issue_body', 'comment', 'event', 'label'
            source_id TEXT NOT NULL,  -- External ID: 'issue:12345', 'comment:abc123'
            source_url TEXT,
            excerpt TEXT,  -- Supporting text
            full_content TEXT,  -- Full source content
            char_start INTEGER,
            char_end INTEGER,
            timestamp TIMESTAMP,
            author_id TEXT REFERENCES entities(id),
            raw_data TEXT,  -- Original API response (JSON)
            content_hash TEXT,  -- For deduplication
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, source_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(content_hash);
        CREATE INDEX IF NOT EXISTS idx_evidence_timestamp ON evidence(timestamp);
        
        -- Claim-Evidence join table
        CREATE TABLE IF NOT EXISTS claim_evidence (
            claim_id TEXT NOT NULL REFERENCES claims(id),
            evidence_id INTEGER NOT NULL REFERENCES evidence(id),
            relevance_score REAL DEFAULT 1.0,
            PRIMARY KEY (claim_id, evidence_id)
        );
        
        -- Merge history for reversibility
        CREATE TABLE IF NOT EXISTS merge_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merge_type TEXT NOT NULL,  -- 'entity', 'claim'
            source_ids TEXT NOT NULL,  -- JSON array of merged IDs
            target_id TEXT NOT NULL,
            reason TEXT,
            confidence REAL,
            automated INTEGER DEFAULT 1,  -- Boolean
            pre_merge_snapshot TEXT,  -- JSON snapshot for restoration
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reversed_at TIMESTAMP
        );
        
        CREATE INDEX IF NOT EXISTS idx_merge_target ON merge_history(target_id);
        
        -- Extraction log for observability
        CREATE TABLE IF NOT EXISTS extraction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            source_id TEXT,
            status TEXT,  -- 'success', 'partial', 'failed'
            claims_extracted INTEGER,
            entities_created INTEGER,
            errors TEXT,  -- JSON
            duration_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Full-text search on evidence excerpts
        CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
            excerpt,
            content='evidence',
            content_rowid='id'
        );
        
        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS evidence_ai AFTER INSERT ON evidence BEGIN
            INSERT INTO evidence_fts(rowid, excerpt) VALUES (new.id, new.excerpt);
        END;
        
        CREATE TRIGGER IF NOT EXISTS evidence_ad AFTER DELETE ON evidence BEGIN
            INSERT INTO evidence_fts(evidence_fts, rowid, excerpt) VALUES('delete', old.id, old.excerpt);
        END;
        
        CREATE TRIGGER IF NOT EXISTS evidence_au AFTER UPDATE ON evidence BEGIN
            INSERT INTO evidence_fts(evidence_fts, rowid, excerpt) VALUES('delete', old.id, old.excerpt);
            INSERT INTO evidence_fts(rowid, excerpt) VALUES (new.id, new.excerpt);
        END;
    """)
    
    conn.commit()
    print(f"Database initialized at {db_path}")
    return conn


def reset_database(db_path: Path = DB_PATH):
    """Drop and recreate all tables"""
    if db_path.exists():
        db_path.unlink()
    return init_database(db_path)


if __name__ == "__main__":
    init_database()
