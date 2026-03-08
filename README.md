# Layer10 Memory Graph

**Grounded Long-Term Memory via Structured Extraction, Deduplication, and a Context Graph**

## Overview

This project implements a memory graph system that transforms GitHub Issues from `microsoft/vscode` into a queryable knowledge graph with:
- **Entities**: People, Issues, Pull Requests, Components
- **Claims**: Relationships with temporal validity and evidence
- **Evidence**: Grounded excerpts from source material

## Demo

📹 **[Watch the demo video](YOUR_DRIVE_OR_LOOM_LINK_HERE)**

The video demonstrates:
- Interactive graph visualization with entity/claim filtering
- Natural language search with evidence grounding
- Expanding evidence panels to view source excerpts and URLs
- Browsing entities by type

## Quick Start

### 1. Setup

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Download spaCy model (optional, for NER)
python -m spacy download en_core_web_sm
```

### 2. Configure

Create a `.env` file with your API keys:

```bash
# Required for LLM extraction
GROQ_API_KEY=your_groq_api_key

# Optional: increases GitHub rate limit from 60 to 5000 req/hr
GITHUB_TOKEN=your_github_token
```

### 3. Run Pipeline

```bash
# Full pipeline (fetch + extract + build graph)
python run_pipeline.py --limit 100

# Skip LLM extraction (faster, pattern-based only)
python run_pipeline.py --limit 100 --no-llm

# Use existing data
python run_pipeline.py --skip-fetch
```

### 4. Launch Visualization

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

## Project Structure

```
Layer10/
├── config.py                 # Configuration settings
├── run_pipeline.py          # Main execution script
├── app.py                   # Streamlit visualization
├── requirements.txt         # Dependencies
├── .env                     # API keys (gitignored)
│
├── src/
│   ├── collection/          # GitHub API data fetching
│   │   └── github_fetcher.py
│   │
│   ├── database/            # SQLite schema and models
│   │   ├── schema.py
│   │   └── models.py
│   │
│   ├── extraction/          # LLM-based extraction
│   │   ├── extractor.py
│   │   └── prompts.py
│   │
│   ├── dedup/               # Deduplication & canonicalization
│   │   └── deduplicator.py
│   │
│   ├── graph/               # Memory graph operations
│   │   └── graph_builder.py
│   │
│   └── retrieval/           # Query & context pack generation
│       └── retriever.py
│
├── data/
│   ├── raw/                 # Raw GitHub API responses
│   ├── processed/           # Processed data
│   └── memory.db            # SQLite database
│
└── outputs/
    ├── memory_graph.json    # Exported graph
    └── sample_context_packs.json
```

## Architecture

### Extraction Tiers

1. **Tier 1 (Structured)**: Direct parsing of GitHub metadata (author, assignees, labels, state)
2. **Tier 2 (Patterns)**: Regex-based extraction (@mentions, #issue refs, decision keywords)
3. **Tier 3 (LLM)**: Groq Llama3 for complex relationships and decisions

### Deduplication Strategy

- **Artifact dedup**: Content hashing for evidence
- **Entity canonicalization**: Alias resolution for people and components
- **Claim dedup**: Signature-based with evidence aggregation
- **Temporal handling**: Validity windows for changing facts

### Memory Graph

- **Storage**: SQLite with FTS5 for full-text search
- **Traversal**: NetworkX for graph operations
- **Visualization**: PyVis interactive graph

## Schema

### Entity Types
- `Person`: GitHub users
- `Issue`: GitHub issues
- `PullRequest`: Pull requests
- `Component`: VS Code areas (terminal, editor, git, etc.)

### Claim Types
- `REPORTED_BY`, `ASSIGNED_TO`, `MENTIONS`
- `AFFECTS_COMPONENT`, `HAS_LABEL`
- `FIXED_BY`, `DUPLICATES`, `BLOCKS`
- `DECISION`, `STATE`

## API Usage

### Query the Graph

```python
from src.graph import MemoryGraph
from src.retrieval import Retriever

graph = MemoryGraph()
graph.build_networkx_graph()

retriever = Retriever(graph)
result = retriever.query("What terminal bugs were fixed?")

print(result.summary)
for claim in result.claims:
    print(f"- {claim.claim_type}: {claim.subject_id}")
```

### Export Data

```python
graph.export_to_json("outputs/my_graph.json")
```

## Evaluation Criteria Mapping

| Criterion | Implementation |
|-----------|---------------|
| **Extraction quality** | 3-tier extraction, validation, versioning |
| **Grounding** | Every claim → evidence chain → source URL |
| **Deduplication** | Content hash, entity canonicalization, claim signatures |
| **Long-term correctness** | Validity windows, superseded claims, soft deletes |
| **Usability** | Streamlit UI, graph view, evidence panel |
| **Clarity** | Modular code, clear documentation |

## Layer10 Adaptation Notes

See the write-up section for how this system would adapt to:
- Email, Slack, Jira/Linear integration
- Cross-system identity resolution
- Permission-aware retrieval
- Operational scaling considerations

## License

MIT
