"""
Main pipeline script - End-to-end execution
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from tqdm import tqdm

from config import (
    RAW_DATA_DIR, PROCESSED_DATA_DIR, OUTPUTS_DIR, DB_PATH,
    ISSUES_TO_FETCH, EXTRACTION_BATCH_SIZE
)
from src.collection import GitHubFetcher
from src.database.schema import init_database, reset_database
from src.database.models import Entity, Claim, Evidence
from src.extraction import Extractor
from src.dedup import Deduplicator
from src.graph import MemoryGraph
from src.retrieval import Retriever


def _log_extraction(conn, run_id: str, source_id: str, status: str, 
                    claims_extracted: int, entities_created: int, 
                    errors: str, duration_ms: int):
    """Log extraction run to database"""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO extraction_log 
            (run_id, source_id, status, claims_extracted, entities_created, errors, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (run_id, source_id, status, claims_extracted, entities_created, errors, duration_ms))
        conn.commit()
    except Exception as e:
        pass  # Don't fail extraction on logging error


def step_1_collect_data(limit: int = ISSUES_TO_FETCH, skip_if_exists: bool = True) -> Path:
    """Step 1: Collect data from GitHub"""
    print("\n" + "="*60)
    print("STEP 1: Collecting data from GitHub")
    print("="*60)
    
    # Check for existing data
    existing_files = list(RAW_DATA_DIR.glob("vscode_issues_*.json"))
    if existing_files and skip_if_exists:
        latest = max(existing_files, key=lambda p: p.stat().st_mtime)
        print(f"Using existing data file: {latest}")
        return latest
    
    fetcher = GitHubFetcher()
    output_file = fetcher.collect_and_save(
        limit=limit,
        include_comments=True,
        include_events=True
    )
    
    return output_file


def step_2_extract_and_build(data_file: Path, use_llm: bool = True) -> MemoryGraph:
    """Step 2: Extract structured data and build graph"""
    print("\n" + "="*60)
    print("STEP 2: Extracting structured data")
    print("="*60)
    
    # Reset database
    reset_database()
    
    # Load raw data
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    issues = data["issues"]
    print(f"Processing {len(issues)} issues...")
    
    # Initialize components
    extractor = Extractor()
    graph = MemoryGraph()  # Initialize graph first to get DB connection
    deduplicator = Deduplicator(conn=graph.conn)  # Pass DB connection for persistence
    
    all_entities = []
    all_claims = []
    all_evidences = []
    extraction_errors = []
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    start_time = time.time()
    
    # Process in batches
    for i in tqdm(range(0, len(issues), EXTRACTION_BATCH_SIZE), desc="Extracting"):
        batch = issues[i:i + EXTRACTION_BATCH_SIZE]
        
        for issue in batch:
            issue_start = time.time()
            issue_num = issue.get('number', '?')
            try:
                entities, claims, evidences = extractor.extract_from_issue(issue, use_llm=use_llm)
                all_entities.extend(entities)
                all_claims.extend(claims)
                all_evidences.extend(evidences)
                
                # Log successful extraction
                _log_extraction(graph.conn, run_id, f"issue:{issue_num}", "success",
                               len(claims), len(entities), None, int((time.time() - issue_start) * 1000))
            except Exception as e:
                error_msg = str(e)
                extraction_errors.append({"issue": issue_num, "error": error_msg})
                print(f"Error processing issue #{issue_num}: {e}")
                
                # Log failed extraction
                _log_extraction(graph.conn, run_id, f"issue:{issue_num}", "failed",
                               0, 0, error_msg, int((time.time() - issue_start) * 1000))
        
        # Rate limit for Groq
        if use_llm:
            time.sleep(0.5)
    
    print(f"\nRaw extraction: {len(all_entities)} entities, {len(all_claims)} claims, {len(all_evidences)} evidences")
    
    # Deduplicate
    print("\nDeduplicating...")
    entities, claims, evidences = deduplicator.process_extraction(all_entities, all_claims, all_evidences)
    print(f"After dedup: {len(entities)} entities, {len(claims)} claims, {len(evidences)} evidences")
    print(f"Dedup stats: {deduplicator.get_statistics()}")
    
    # Build graph
    print("\nBuilding memory graph...")
    
    # Add entities
    for entity in tqdm(entities, desc="Adding entities"):
        graph.add_entity(entity)
    
    # Now persist aliases and merge history (entities must exist first)
    aliases_persisted = deduplicator.persist_all_aliases()
    merges_persisted = deduplicator.persist_all_merge_history()
    print(f"Persisted {aliases_persisted} aliases, {merges_persisted} merge records")
    
    # Add evidences and get IDs
    evidence_map = {}  # source_id -> db_id
    for evidence in tqdm(evidences, desc="Adding evidence"):
        ev_id = graph.add_evidence(evidence)
        evidence_map[evidence.source_id] = ev_id
    
    # Add claims with evidence links
    for claim in tqdm(claims, desc="Adding claims"):
        ev_ids = [evidence_map.get(e.source_id) for e in claim.evidence if e.source_id in evidence_map]
        ev_ids = [eid for eid in ev_ids if eid is not None]
        graph.add_claim(claim, ev_ids)
    
    # Build NetworkX graph
    graph.build_networkx_graph()
    
    print(f"\nGraph statistics: {graph.get_statistics()}")
    
    return graph


def step_3_export(graph: MemoryGraph) -> Path:
    """Step 3: Export graph to JSON"""
    print("\n" + "="*60)
    print("STEP 3: Exporting graph")
    print("="*60)
    
    output_path = OUTPUTS_DIR / "memory_graph.json"
    graph.export_to_json(output_path)
    print(f"Exported to: {output_path}")
    
    return output_path


def step_4_sample_queries(graph: MemoryGraph) -> Path:
    """Step 4: Generate sample context packs"""
    print("\n" + "="*60)
    print("STEP 4: Generating sample queries")
    print("="*60)
    
    retriever = Retriever(graph)
    
    sample_queries = [
        "What terminal bugs were reported recently?",
        "Which issues were fixed by pull requests?",
        "What decisions were made about feature requests?",
        "Who is working on editor-related issues?",
        "What components have the most bugs?",
    ]
    
    results = []
    
    for query in sample_queries:
        print(f"\nQuery: {query}")
        try:
            result = retriever.query(query)
            results.append({
                "query": query,
                "result": result.to_dict()
            })
            print(f"  Found: {len(result.claims)} claims, {len(result.entities)} entities")
            print(f"  Confidence: {result.confidence:.2f}")
        except Exception as e:
            print(f"  Error: {e}")
    
    # Save results
    output_path = OUTPUTS_DIR / "sample_context_packs.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\nSaved sample context packs to: {output_path}")
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Run the Layer10 memory graph pipeline")
    parser.add_argument("--limit", type=int, default=100, help="Number of issues to fetch (default: 100)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM extraction (faster, less detailed)")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip fetching if data exists")
    parser.add_argument("--step", type=int, help="Run only specific step (1-4)")
    args = parser.parse_args()
    
    start_time = datetime.now()
    print(f"\n{'='*60}")
    print(f"Layer10 Memory Graph Pipeline")
    print(f"Started at: {start_time}")
    print(f"{'='*60}")
    
    # Step 1: Collect data
    if args.step is None or args.step == 1:
        data_file = step_1_collect_data(limit=args.limit, skip_if_exists=args.skip_fetch)
    else:
        # Find latest data file
        existing_files = list(RAW_DATA_DIR.glob("vscode_issues_*.json"))
        if not existing_files:
            print("No data file found. Run step 1 first.")
            return
        data_file = max(existing_files, key=lambda p: p.stat().st_mtime)
    
    # Step 2: Extract and build
    if args.step is None or args.step == 2:
        graph = step_2_extract_and_build(data_file, use_llm=not args.no_llm)
    else:
        graph = MemoryGraph()
        graph.build_networkx_graph()
    
    # Step 3: Export
    if args.step is None or args.step == 3:
        step_3_export(graph)
    
    # Step 4: Sample queries
    if args.step is None or args.step == 4:
        step_4_sample_queries(graph)
    
    # Cleanup
    graph.close()
    
    end_time = datetime.now()
    print(f"\n{'='*60}")
    print(f"Pipeline completed!")
    print(f"Duration: {end_time - start_time}")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    print(f"  1. View the graph: streamlit run app.py")
    print(f"  2. Check outputs in: {OUTPUTS_DIR}")


if __name__ == "__main__":
    main()
