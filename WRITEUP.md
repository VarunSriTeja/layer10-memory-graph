# Grounded Long-Term Memory System: Technical Write-Up

## 1. Ontology and Extraction Contract

### 1.1 Core Schema

The system uses a structured ontology designed for extracting and storing knowledge from GitHub Issues:

**Entities** represent discrete, identifiable objects:
| Type | Description | Examples |
|------|-------------|----------|
| Person | GitHub users, contributors, maintainers | @bpasero, @sandy081 |
| Component | Software modules, features, systems | "Terminal", "Extensions View", "Settings Editor" |
| Interface | APIs, protocols, external systems | "Language Server Protocol", "Git API" |
| Artifact | Identifiable outputs | "Issue #12345", "PR #9876", "Extension v1.2.3" |

**Claims** represent facts/relationships extracted from text:
| Type | Description | Example |
|------|-------------|---------|
| OWNS | Ownership/responsibility | "bpasero owns Terminal component" |
| WORKS_ON | Active involvement | "sandy081 works on Extensions" |
| REPORTED | Issue reporting | "user123 reported bug #456" |
| DECIDED | Architectural decisions | "Team decided to use LSP" |
| STATUS | Component state | "Terminal is production-ready" |
| BLOCKS | Dependency relationships | "Issue A blocks Issue B" |
| MENTIONS | Reference relationships | "Discussion mentions performance" |

**Evidence** grounds every claim to source text:
- `source_type`: "github_issue", "github_comment", etc.
- `source_url`: Direct link to the original content
- `excerpt`: Exact quoted text (with char_start/char_end positions)
- `content_hash`: SHA256 for deduplication and integrity

### 1.2 Extraction Contract

The extraction system follows a 3-tier pipeline with strict contracts:

**Tier 1: Structured Extraction** (Highest confidence: 0.95)
- Extracts metadata with deterministic patterns
- Sources: Issue labels, assignees, milestone dates, GitHub API fields
- Contract: Always produces evidence with exact source location

**Tier 2: Pattern-Based Extraction** (Medium confidence: 0.75-0.85)
- Uses regex patterns for semi-structured content
- Patterns: `@mentions`, `#references`, `component: xyz`, `status: done`
- Contract: Confidence adjusted by pattern specificity

**Tier 3: LLM Extraction** (Variable confidence: 0.5-0.9)
- Natural language understanding for implicit relationships
- Prompt instructs: "Only extract facts explicitly stated"
- Contract: Must cite exact excerpt from source text

**Quality Gates:**
- Minimum confidence threshold: 0.5 (configurable via CONFIDENCE_THRESHOLD)
- Evidence requirement: Claims without evidence are rejected
- Entity validation: All entity IDs must follow `type:identifier` format

---

## 2. Deduplication Strategy

### 2.1 Multi-Level Deduplication

The system employs three complementary deduplication strategies:

**Content Hash Deduplication (Evidence)**
```
hash = SHA256(normalize(full_content))
```
- Applied at ingestion time
- Catches exact duplicates across documents
- Preserves first occurrence with all metadata

**Claim Signature Deduplication**
```
signature = SHA256(claim_type + subject_id + object_id + canonical(value))
```
- Identifies semantically identical claims
- When duplicates found:
  - Merge evidence lists (union)
  - Boost confidence (+0.05 per additional source, capped at 1.0)
  - Keep earliest validity_start timestamp

**Entity Canonicalization**
```
"@bpasero" → "person:bpasero"
"bpasero" → "person:bpasero"
"Benjamin Pasero" → "person:bpasero"
```
- Normalizes entity references to canonical IDs
- Maintains alias index for lookup
- Handles case variations, @ prefixes, common nicknames

### 2.2 Merge Operations

When entities are merged:
1. **Pre-merge snapshot captured** for reversibility
2. All claims referencing source entities updated to target
3. Alias mappings preserved for future lookups
4. Merge record created with:
   - `source_ids`: Original entity IDs
   - `target_id`: Canonical entity ID
   - `reason`: "same_person_different_names", "component_alias", etc.
   - `reversed_at`: Null (set if merge is undone)

### 2.3 Handling Updates

When the same information appears again:
- **Same content, same source**: Deduplicated by content hash (no action)
- **Same claim, new source**: Evidence merged, confidence boosted
- **Contradicting claim**: Both stored with separate validity ranges

---

## 3. Update Semantics

### 3.1 Temporal Handling

Every claim has temporal metadata:
- `validity_start`: When the claim became true (from source timestamp)
- `validity_end`: When the claim was superseded (null if current)
- `superseded_by`: ID of the newer claim (null if current)

**Supersession Process:**
```python
supersede_claim(old_id, new_claim):
    old_claim.validity_end = now()
    old_claim.superseded_by = new_claim.id
    store(new_claim)  # new claim has validity_start = now
```

**Query Behavior:**
- Default: `WHERE validity_end IS NULL` (current facts only)
- Historical: Can query "as of" specific timestamps
- Conflict detection: Flags multiple current claims for same (subject, type)

### 3.2 Versioning

**Extraction Versioning:**
- `extraction_version`: Tracks which extractor version produced the claim
- Enables re-extraction with improved models without losing provenance

**Claim Versioning:**
- `version`: Integer increment on updates
- Enables optimistic concurrency control

### 3.3 Soft Delete

Entities support soft delete via `deleted_at` timestamp:
- Queries filter by `WHERE deleted_at IS NULL`
- Historical queries can include deleted entities
- Enables undo/recovery operations

### 3.4 Idempotency

Processing the same document multiple times:
1. Content hash blocks duplicate evidence
2. Claim signatures block duplicate claims
3. Entity canonicalization ensures consistent IDs
4. Net effect: System state unchanged after reprocessing

---

## 4. Adapting for Layer10's Use Case

Layer10 needs to extract and maintain memory from diverse enterprise communication sources. Here's how this system would adapt:

### 4.1 Email Integration

**Source Mapping:**
| GitHub Concept | Email Equivalent |
|----------------|------------------|
| Issue body | Email body |
| Comment | Reply in thread |
| @mention | To/CC recipients |
| Author | From sender |
| Created date | Sent timestamp |
| Labels | Subject keywords, folder |

**Additional Patterns Needed:**
- Thread detection (In-Reply-To, References headers)
- Quoted content exclusion (lines starting with `>`)
- Signature detection and removal
- Forward chain parsing

**New Claim Types:**
- REQUESTED: "Alice requested budget approval from Bob"
- COMMITTED: "Charlie committed to delivering by Friday"
- DELEGATED: "Manager delegated task to team member"

**Challenges:**
- Email threads can span months with context scattered
- Need sliding window with context carryover
- PII handling more critical (names, contact info)

### 4.2 Slack Integration

**Source Mapping:**
| GitHub Concept | Slack Equivalent |
|----------------|------------------|
| Issue | Thread parent message |
| Comment | Thread reply |
| @mention | @user or @channel |
| Repository | Channel/Workspace |
| Labels | Emoji reactions, channel name |

**Extraction Adjustments:**
- Very short messages require aggregation
- Emoji reactions carry semantic meaning (✅ = done, 👀 = reviewing)
- Message edits create version history
- Bot messages often contain structured data

**New Patterns:**
```regex
/done|completed|shipped|LGTM/i → STATUS:done
/blocking|blocker|stuck on/i → BLOCKS relationship
/meeting|sync|standup/i → EVENT claim type
```

**Challenges:**
- High volume, low signal-to-noise ratio
- Real-time updates require streaming architecture
- Cross-channel context (discussion in #eng, decision in #leadership)

### 4.3 Jira/Linear Integration

**Advantages:**
- Highly structured data (fields, statuses, workflows)
- Explicit relationships (blocks, relates to, parent/child)
- Rich metadata (story points, sprints, components)

**Source Mapping:**
| GitHub Concept | Jira/Linear Equivalent |
|----------------|------------------------|
| Issue | Ticket/Issue |
| Labels | Labels, Components, Story Type |
| Assignee | Assignee |
| Milestone | Sprint, Epic |
| Comments | Comments |
| State | Status (with history) |

**Tier 1 Extraction Boost:**
- Status transitions: Built-in history log
- Relationships: First-class API concepts
- Assignments: Explicit, timestamped changes

**Extraction Contract:**
```json
{
  "transitions": [
    {"from": "In Progress", "to": "Done", "date": "2024-01-15", "actor": "alice"}
  ],
  "claims": [
    {"type": "STATUS", "value": "Done", "evidence": "Jira transition log", "confidence": 0.98}
  ]
}
```

**Challenges:**
- Field customization varies by org
- Epic/story/task hierarchy semantics differ
- Integration with existing ticket linking

### 4.4 Cross-Source Correlation

The most powerful capability: linking knowledge across sources.

**Entity Resolution:**
```
Email: "alice@company.com"
Slack: "@alice.smith"
Jira: "asmith"
→ All resolve to: person:alice_smith
```

**Cross-Source Inference:**
1. Email: "Let's discuss the auth redesign in tomorrow's meeting"
2. Slack meeting notes: "Decided to use OAuth2"
3. Jira ticket: AUTH-123 created

**Claim chain:**
- PROPOSED (email) → DECIDED (slack) → IMPLEMENTED (jira)
- Each grounded to specific evidence
- Conflict detection: If Jira status contradicts Slack decision

### 4.5 Scaling Considerations

**For Production Layer10 Deployment:**

1. **Incremental Updates**: Webhook-driven rather than batch polling
2. **Real-time Dedup**: Redis-backed signature cache for sub-ms lookups
3. **Embedding Index**: Vector DB (Pinecone/Weaviate) for semantic search
4. **Multi-tenant**: Workspace isolation with shared entity resolution
5. **Privacy Controls**: PII detection, redaction, retention policies
6. **Streaming Extraction**: Kafka-based pipeline for high-volume Slack

**Performance Targets:**
- Evidence lookup: <10ms (FTS5 + hash index)
- Entity resolution: <5ms (in-memory alias cache)
- Claim insertion: <50ms (including dedup)
- Context pack generation: <500ms (retrieval + ranking)

---

## Appendix: Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Data Sources                            │
├─────────────┬─────────────┬─────────────┬─────────────┬────────┤
│   GitHub    │    Email    │    Slack    │ Jira/Linear │  ...   │
└──────┬──────┴──────┬──────┴──────┬──────┴──────┬──────┴────────┘
       │             │             │             │
       └─────────────┴──────┬──────┴─────────────┘
                            │
                   ┌────────▼────────┐
                   │   Extraction     │
                   │   (3-Tier)       │
                   │  ┌────────────┐  │
                   │  │ Structured │  │
                   │  │  Patterns  │  │
                   │  │    LLM     │  │
                   │  └────────────┘  │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │  Deduplication   │
                   │  ┌────────────┐  │
                   │  │Content Hash│  │
                   │  │ Signatures │  │
                   │  │Canonicaliz.│  │
                   │  └────────────┘  │
                   └────────┬────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼───────┐   ┌───────▼───────┐   ┌───────▼───────┐
│   SQLite DB   │   │   NetworkX    │   │  Embeddings   │
│  (Entities,   │   │    Graph      │   │    Index      │
│   Claims,     │   │  (Traversal)  │   │  (Semantic)   │
│  Evidence)    │   │               │   │               │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                   ┌────────▼────────┐
                   │    Retrieval     │
                   │  ┌────────────┐  │
                   │  │  Keyword   │  │
                   │  │  Semantic  │  │
                   │  │   Graph    │  │
                   │  └────────────┘  │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │  Context Pack   │
                   │   Generation    │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │   LLM / User    │
                   │   Interface     │
                   └─────────────────┘
```

---

## Summary

This implementation demonstrates a complete grounded memory system with:

1. **Structured Ontology**: Clear entity types, claim types, and evidence linking
2. **Multi-Tier Extraction**: Balancing precision (structured) with recall (LLM)
3. **Robust Deduplication**: Content hash, signature, and entity canonicalization
4. **Temporal Semantics**: Full claim lifecycle with supersession
5. **Hybrid Retrieval**: Keyword + semantic + graph traversal
6. **Evidence Grounding**: Every claim traceable to source text

The architecture readily extends to Layer10's enterprise communication use case, with natural mappings from GitHub constructs to email, Slack, and Jira/Linear sources.
