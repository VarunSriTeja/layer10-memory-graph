"""
Streamlit visualization for the Memory Graph
"""
import json
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))

import streamlit as st
from pyvis.network import Network
import networkx as nx

from config import DB_PATH, OUTPUTS_DIR
from src.graph import MemoryGraph
from src.retrieval import Retriever, ContextPack
from src.database.models import Entity, Claim, Evidence


# Page config
st.set_page_config(
    page_title="Layer10 Memory Graph",
    page_icon="🧠",
    layout="wide"
)

# Custom CSS for styling
st.markdown("""
<style>
    .entity-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.8em;
        font-weight: bold;
        margin-right: 5px;
    }
    .badge-person { background-color: #4CAF50; color: white; }
    .badge-issue { background-color: #FFC107; color: black; }
    .badge-pullrequest { background-color: #2196F3; color: white; }
    .badge-component { background-color: #9C27B0; color: white; }
    .evidence-box {
        background-color: #1e1e2e;
        border-left: 3px solid #4CAF50;
        padding: 10px;
        margin: 5px 0;
        border-radius: 4px;
    }
    .confidence-high { color: #4CAF50; }
    .confidence-medium { color: #FFC107; }
    .confidence-low { color: #f44336; }
    .stat-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        color: white;
    }
    .legend-item {
        display: inline-flex;
        align-items: center;
        margin-right: 15px;
        font-size: 0.9em;
    }
    .legend-dot {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        margin-right: 5px;
    }
</style>
""", unsafe_allow_html=True)

# Color scheme
TYPE_COLORS = {
    "Person": "#4CAF50",
    "Issue": "#FFC107",
    "PullRequest": "#2196F3",
    "Component": "#9C27B0",
}

CLAIM_COLORS = {
    "AFFECTS_COMPONENT": "#9C27B0",
    "ASSIGNED_TO": "#4CAF50",
    "REPORTED_BY": "#2196F3",
    "HAS_LABEL": "#FF9800",
    "REFERENCES": "#607D8B",
    "MENTIONS": "#00BCD4",
    "DECISION": "#E91E63",
    "STATE": "#795548",
}


@st.cache_resource
def get_graph():
    """Load memory graph (cached)"""
    # Create a fresh connection that allows cross-thread access
    graph = MemoryGraph()
    # Rebuild connection with check_same_thread=False
    import sqlite3
    graph.conn = sqlite3.connect(graph.db_path, check_same_thread=False)
    graph.conn.row_factory = sqlite3.Row
    graph.build_networkx_graph()
    return graph


@st.cache_resource
def get_retriever(_graph):
    """Initialize retriever (cached)"""
    return Retriever(_graph)


def render_pyvis_graph(graph: MemoryGraph, entity_filter: list = None, claim_filter: list = None, height: int = 550):
    """Render interactive graph using PyVis"""
    
    # Create PyVis network
    net = Network(height=f"{height}px", width="100%", bgcolor="#0e1117", font_color="white")
    net.toggle_physics(True)
    net.set_options("""
    {
        "nodes": {
            "font": {"size": 14, "color": "white"},
            "borderWidth": 2,
            "borderWidthSelected": 4
        },
        "edges": {
            "color": {"color": "#555555", "highlight": "#ffffff"},
            "font": {"size": 10, "color": "#aaaaaa", "strokeWidth": 0},
            "arrows": {"to": {"enabled": true, "scaleFactor": 0.5}},
            "smooth": {"type": "continuous"}
        },
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -50,
                "centralGravity": 0.01,
                "springLength": 120,
                "springConstant": 0.08
            },
            "solver": "forceAtlas2Based",
            "stabilization": {"iterations": 100}
        },
        "interaction": {
            "hover": true,
            "tooltipDelay": 100
        }
    }
    """)
    
    # Add nodes
    for node_id, data in graph.graph.nodes(data=True):
        entity_type = data.get("type", "Unknown")
        
        # Apply filter
        if entity_filter and entity_type not in entity_filter:
            continue
        
        color = TYPE_COLORS.get(entity_type, "#666666")
        name = data.get("canonical_name", node_id)
        
        net.add_node(
            node_id,
            label=name[:25] + "..." if len(name) > 25 else name,
            title=f"<b>{entity_type}</b><br>{name}<br><i>{node_id}</i>",
            color=color,
            size=25 if entity_type == "Issue" else 18,
            shape="dot" if entity_type == "Person" else "box" if entity_type == "Issue" else "diamond"
        )
    
    # Add edges
    for u, v, data in graph.graph.edges(data=True):
        claim_type = data.get("claim_type", "RELATED")
        
        # Apply claim filter
        if claim_filter and claim_type not in claim_filter:
            continue
        
        # Only add edge if both nodes exist
        if u in [n["id"] for n in net.nodes] and v in [n["id"] for n in net.nodes]:
            edge_color = CLAIM_COLORS.get(claim_type, "#888888")
            net.add_edge(
                u, v,
                title=f"<b>{claim_type}</b>",
                label=claim_type[:12],
                color=edge_color,
                width=2
            )
    
    return net


def render_graph_legend():
    """Render color legend for the graph"""
    st.markdown("**Legend:**")
    cols = st.columns(len(TYPE_COLORS))
    for i, (entity_type, color) in enumerate(TYPE_COLORS.items()):
        with cols[i]:
            st.markdown(
                f'<span class="legend-item"><span class="legend-dot" style="background-color: {color};"></span>{entity_type}</span>',
                unsafe_allow_html=True
            )


def get_entity_badge(entity_type: str) -> str:
    """Generate HTML badge for entity type"""
    badge_class = f"badge-{entity_type.lower()}"
    return f'<span class="entity-badge {badge_class}">{entity_type}</span>'


def get_confidence_class(confidence: float) -> str:
    """Get CSS class based on confidence level"""
    if confidence >= 0.8:
        return "confidence-high"
    elif confidence >= 0.5:
        return "confidence-medium"
    return "confidence-low"


def render_entity_details(graph: MemoryGraph, entity_id: str):
    """Render entity details panel"""
    entity = graph.get_entity(entity_id)
    
    if not entity:
        st.warning(f"Entity not found: {entity_id}")
        return
    
    st.markdown(f"{get_entity_badge(entity.type)} **{entity.canonical_name}**", unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.caption(f"ID: `{entity.id}`")
    
    with col2:
        if entity.properties:
            props = ", ".join([f"{k}: {v}" for k, v in list(entity.properties.items())[:3]])
            st.caption(props)
    
    # Show claims
    claims = graph.get_claims_for_entity(entity_id, current_only=True)
    
    if claims:
        st.markdown("**Related Claims:**")
        for claim in claims[:10]:
            conf_class = get_confidence_class(claim.confidence)
            with st.expander(f"🔗 {claim.claim_type} → {claim.object_id or 'N/A'}"):
                st.markdown(f"Confidence: <span class='{conf_class}'>{claim.confidence:.0%}</span>", unsafe_allow_html=True)
                
                if claim.value:
                    st.json(claim.value)
                
                # Evidence with better styling
                if claim.evidence:
                    st.markdown("**📄 Evidence:**")
                    for ev in claim.evidence[:3]:
                        excerpt = ev.excerpt[:250] if ev.excerpt else "No excerpt"
                        st.markdown(f'<div class="evidence-box">{excerpt}{"..." if len(ev.excerpt or "") > 250 else ""}</div>', unsafe_allow_html=True)
                        if ev.source_url:
                            st.markdown(f"🔗 [View Source]({ev.source_url})")


def render_search_results(context_pack: ContextPack):
    """Render search results with modern styling"""
    
    # Summary header
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        st.markdown(f"### 🔍 {context_pack.summary}")
    with col2:
        conf_class = get_confidence_class(context_pack.confidence)
        st.metric("Confidence", f"{context_pack.confidence:.0%}")
    with col3:
        st.metric("Claims", len(context_pack.claims))
    
    # Metadata expander
    with st.expander("📊 Query Metadata", expanded=False):
        st.json(context_pack.metadata)
    
    # Entities with badges
    if context_pack.entities:
        st.markdown("---")
        st.markdown("**📦 Relevant Entities:**")
        entity_html = ""
        for entity in context_pack.entities[:12]:
            entity_html += f'{get_entity_badge(entity.type)} {entity.canonical_name} &nbsp; '
        st.markdown(entity_html, unsafe_allow_html=True)
    
    # Claims with evidence
    if context_pack.claims:
        st.markdown("---")
        st.markdown("**📋 Claims with Evidence:**")
        
        for claim in context_pack.claims[:15]:
            conf_class = get_confidence_class(claim.confidence)
            header = f"{claim.claim_type}: {claim.subject_id} → {claim.object_id or 'N/A'}"
            
            with st.expander(header, expanded=False):
                st.markdown(f"**Confidence:** <span class='{conf_class}'>{claim.confidence:.0%}</span>", unsafe_allow_html=True)
                
                if claim.value:
                    st.json(claim.value)
                
                # Evidence boxes
                if claim.evidence:
                    for ev in claim.evidence[:2]:
                        excerpt = ev.excerpt[:300] if ev.excerpt else "No excerpt available"
                        st.markdown(f'<div class="evidence-box">{excerpt}</div>', unsafe_allow_html=True)
                        if ev.source_url:
                            st.markdown(f"[📎 View Source]({ev.source_url})")
                else:
                    st.caption("No evidence linked to this claim")
    
    # Citations
    if context_pack.citations:
        st.markdown("---")
        with st.expander("📚 Citations", expanded=False):
            for i, citation in enumerate(context_pack.citations[:10], 1):
                st.markdown(f"[{i}] {citation}")
    
    # Ambiguities/Conflicts
    if context_pack.ambiguities:
        st.markdown("---")
        st.warning("⚠️ **Conflicting Information Detected**")
        for amb in context_pack.ambiguities:
            st.markdown(f"• **{amb['claim_type']}** for `{amb['subject']}`: {amb['conflicting_values']}")


def main():
    st.title("🧠 Layer10 Memory Graph")
    st.caption("Grounded Long-Term Memory via Structured Extraction & Context Graph")
    
    # Load graph
    try:
        graph = get_graph()
        retriever = get_retriever(graph)
        stats = graph.get_statistics()
    except Exception as e:
        st.error(f"Failed to load graph: {e}")
        st.info("Run the pipeline first: `python run_pipeline.py`")
        return
    
    # Sidebar - Statistics only (filters moved to Graph tab)
    with st.sidebar:
        st.header("📊 Statistics")
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Entities", stats.get("total_entities", 0))
            st.metric("Claims", stats.get("total_claims", 0))
        with col2:
            st.metric("Evidence", stats.get("total_evidence", 0))
            st.metric("Graph Edges", stats.get("graph_edges", 0))
        
        st.markdown("---")
        
        # Entity breakdown
        st.markdown("**Entity Types:**")
        for etype, count in stats.get("entities_by_type", {}).items():
            color = TYPE_COLORS.get(etype, "#666")
            st.markdown(f'<span style="color: {color};">●</span> {etype}: **{count}**', unsafe_allow_html=True)
        
        st.markdown("---")
        
        # Claim breakdown
        st.markdown("**Top Claim Types:**")
        claims_by_type = stats.get("claims_by_type", {})
        sorted_claims = sorted(claims_by_type.items(), key=lambda x: x[1], reverse=True)[:5]
        for ctype, count in sorted_claims:
            st.caption(f"{ctype}: {count}")
    
    # Main content - Tabs
    tab1, tab2, tab3 = st.tabs(["🔍 Search", "🕸 Graph View", "📋 Browse"])
    
    with tab1:
        st.markdown("### Query the Memory Graph")
        st.caption("Ask questions in natural language. The system understands time filters, entity types, and relationships.")
        
        # Initialize session state for query
        if "search_query" not in st.session_state:
            st.session_state.search_query = ""
        if "trigger_search" not in st.session_state:
            st.session_state.trigger_search = False
        if "last_search_result" not in st.session_state:
            st.session_state.last_search_result = None
        
        # Example queries
        example_queries = [
            "terminal bugs recently",
            "what decisions were made?",
            "editor performance issues",
            "show all components",
        ]
        
        col1, col2, col3, col4 = st.columns(4)
        for i, example in enumerate(example_queries):
            with [col1, col2, col3, col4][i]:
                if st.button(f"💡 {example[:18]}...", key=f"ex_{i}", use_container_width=True):
                    st.session_state.search_query = example
                    st.session_state.trigger_search = True
        
        query = st.text_input(
            "Your question:",
            value=st.session_state.search_query,
            placeholder="What terminal bugs were reported recently?",
            key="query_input",
            on_change=lambda: setattr(st.session_state, 'search_query', st.session_state.query_input)
        )
        
        search_btn = st.button("🔍 Search", type="primary", use_container_width=False)
        
        # Trigger search on button click OR when example was clicked
        should_search = search_btn or st.session_state.trigger_search
        
        if should_search and st.session_state.search_query:
            st.session_state.trigger_search = False  # Reset trigger
            with st.spinner("Searching knowledge graph..."):
                try:
                    result = retriever.query(st.session_state.search_query)
                    st.session_state.last_search_result = result
                except Exception as e:
                    st.error(f"Search failed: {e}")
                    st.exception(e)
        
        # Always show last search result if available
        if st.session_state.last_search_result:
            render_search_results(st.session_state.last_search_result)
    
    with tab2:
        st.markdown("### Interactive Knowledge Graph")
        
        # Filters in Graph View tab only
        with st.container():
            col1, col2, col3 = st.columns([2, 2, 1])
            
            entity_types = list(stats.get("entities_by_type", {}).keys())
            claim_types = list(stats.get("claims_by_type", {}).keys())
            
            with col1:
                selected_types = st.multiselect(
                    "🎯 Filter Entity Types",
                    entity_types,
                    default=entity_types,
                    help="Select which entity types to show"
                )
            
            with col2:
                selected_claims = st.multiselect(
                    "🔗 Filter Claim Types",
                    claim_types,
                    default=claim_types[:5] if len(claim_types) > 5 else claim_types,
                    help="Select which relationship types to show"
                )
            
            with col3:
                graph_height = st.slider(
                    "📐 Graph Height",
                    min_value=400,
                    max_value=900,
                    value=570,
                    step=50,
                    help="Adjust the graph display height"
                )
        
        # Legend
        render_graph_legend()
        
        if stats.get("total_entities", 0) > 0:
            with st.spinner("Rendering graph..."):
                net = render_pyvis_graph(
                    graph,
                    entity_filter=selected_types,
                    claim_filter=selected_claims,
                    height=graph_height
                )
                
                # Save and display
                html_path = OUTPUTS_DIR / "graph.html"
                net.save_graph(str(html_path))
                
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                
                st.components.v1.html(html_content, height=graph_height + 20, scrolling=True)
                
            st.caption("💡 Tip: Drag nodes to rearrange. Hover for details. Scroll to zoom.")
        else:
            st.info("No data in graph. Run the pipeline first.")
    
    with tab3:
        st.markdown("### Browse Entities")
        
        entity_types = list(stats.get("entities_by_type", {}).keys())
        
        col1, col2 = st.columns([1, 2])
        with col1:
            browse_type = st.selectbox("Entity Type", entity_types if entity_types else ["None"])
        with col2:
            search = st.text_input("🔎 Filter by name:", key="browse_filter")
        
        if browse_type and browse_type != "None":
            entities = graph.get_entities_by_type(browse_type)
            
            if search:
                entities = [e for e in entities if search.lower() in e.canonical_name.lower()]
            
            if entities:
                st.markdown(f"**Found {len(entities)} {browse_type}(s):**")
                
                for entity in entities[:50]:
                    with st.expander(f"{entity.canonical_name}"):
                        render_entity_details(graph, entity.id)
            else:
                st.info(f"No {browse_type} entities found matching your filter.")


if __name__ == "__main__":
    main()
