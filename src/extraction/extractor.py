"""
Extraction pipeline using Groq LLM
"""
import json
import re
import hashlib
import time
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_MAX_TOKENS, GROQ_TEMPERATURE, CONFIDENCE_THRESHOLD
from src.database.models import Entity, Claim, Evidence
from src.extraction.prompts import EXTRACTION_PROMPT, COMPONENT_LABELS, DECISION_PATTERNS


class Extractor:
    """Extract structured information from GitHub issues using Groq LLM"""
    
    def __init__(self, api_key: str = GROQ_API_KEY):
        self.client = Groq(api_key=api_key)
        self.model = GROQ_MODEL
        self.extraction_version = f"{GROQ_MODEL}_v1"
    
    def _call_llm(self, prompt: str) -> str:
        """Call Groq LLM with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a precise JSON extraction assistant. Always return valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=GROQ_TEMPERATURE,
                    max_tokens=GROQ_MAX_TOKENS,
                )
                return response.choices[0].message.content
            except Exception as e:
                if "rate_limit" in str(e).lower():
                    wait_time = 2 ** attempt
                    print(f"Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
        raise Exception("Max retries exceeded")
    
    def _parse_llm_response(self, response: str) -> dict:
        """Parse LLM response to JSON, handling common issues"""
        if not response:
            return {"entities": [], "claims": [], "error": "Empty response"}
        
        # Try to extract JSON from response
        response = response.strip()
        
        # Handle markdown code blocks
        if "```json" in response:
            match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
            if match:
                response = match.group(1)
        elif "```" in response:
            match = re.search(r"```\s*(.*?)\s*```", response, re.DOTALL)
            if match:
                response = match.group(1)
        
        # Clean up common issues
        response = response.strip()
        
        # Try direct parse
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Try to find JSON object in response
        match = re.search(r"\{[\s\S]*\}", response)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        # Try to fix common JSON issues
        try:
            # Replace single quotes with double quotes
            fixed = response.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        return {"entities": [], "claims": [], "error": f"Failed to parse JSON: {response[:100]}"}
    
    def _extract_tier1_structured(self, issue: dict) -> Tuple[List[Entity], List[Claim], List[Evidence]]:
        """
        Tier 1: Extract from structured data (no LLM needed)
        - Issue metadata, author, assignees, labels, state
        """
        entities = []
        claims = []
        evidences = []
        
        issue_number = issue["number"]
        issue_id = f"issue:{issue_number}"
        
        # Create issue entity
        issue_entity = Entity(
            id=issue_id,
            type="Issue",
            canonical_name=issue["title"][:200],
            properties={
                "number": issue_number,
                "state": issue["state"],
                "url": issue["html_url"],
                "created_at": issue["created_at"],
                "closed_at": issue.get("closed_at"),
                "comments_count": issue.get("comments", 0),
            }
        )
        entities.append(issue_entity)
        
        # Create evidence from issue body
        body_evidence = Evidence(
            source_type="issue_body",
            source_id=f"issue:{issue_number}:body",
            source_url=issue["html_url"],
            excerpt=issue.get("body", "")[:500] if issue.get("body") else None,
            full_content=issue.get("body"),
            timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
            author_id=f"person:{issue['user']['login']}",
            raw_data=issue,
            content_hash=hashlib.sha256(
                (issue.get("body") or "").encode()
            ).hexdigest()
        )
        evidences.append(body_evidence)
        
        # Author entity and REPORTED_BY claim
        author = issue["user"]
        author_entity = Entity(
            id=f"person:{author['login']}",
            type="Person",
            canonical_name=author["login"],
            properties={
                "github_id": author["id"],
                "url": author["html_url"],
                "avatar_url": author.get("avatar_url"),
            }
        )
        entities.append(author_entity)
        
        reported_by_claim = Claim(
            id=f"claim:{issue_id}-reported-by-{author['login']}",
            claim_type="REPORTED_BY",
            subject_id=issue_id,
            object_id=f"person:{author['login']}",
            confidence=1.0,
            validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
            extraction_version=self.extraction_version,
            evidence=[body_evidence]  # Link to issue body as evidence
        )
        claims.append(reported_by_claim)
        
        # Assignees
        for assignee in issue.get("assignees", []):
            assignee_entity = Entity(
                id=f"person:{assignee['login']}",
                type="Person",
                canonical_name=assignee["login"],
                properties={
                    "github_id": assignee["id"],
                    "url": assignee["html_url"],
                }
            )
            entities.append(assignee_entity)
            
            # Create evidence for assignment
            assign_evidence = Evidence(
                source_type="issue_metadata",
                source_id=f"issue:{issue_number}:assignee:{assignee['login']}",
                source_url=issue["html_url"],
                excerpt=f"Assigned to @{assignee['login']}",
                timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
            )
            evidences.append(assign_evidence)
            
            assigned_claim = Claim(
                id=f"claim:{issue_id}-assigned-to-{assignee['login']}",
                claim_type="ASSIGNED_TO",
                subject_id=issue_id,
                object_id=f"person:{assignee['login']}",
                confidence=1.0,
                validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                extraction_version=self.extraction_version,
                evidence=[assign_evidence]
            )
            claims.append(assigned_claim)
        
        # Labels → Components
        for label in issue.get("labels", []):
            label_name = label["name"].lower()
            
            # Check if label maps to a component
            for key, component_name in COMPONENT_LABELS.items():
                if key in label_name:
                    component_entity = Entity(
                        id=f"component:{key}",
                        type="Component",
                        canonical_name=component_name,
                        properties={"label": label["name"]}
                    )
                    entities.append(component_entity)
                    
                    # Create evidence for component
                    component_evidence = Evidence(
                        source_type="issue_label",
                        source_id=f"issue:{issue_number}:label:{label['name']}",
                        source_url=issue["html_url"],
                        excerpt=f"Label: {label['name']}",
                        timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                    )
                    evidences.append(component_evidence)
                    
                    affects_claim = Claim(
                        id=f"claim:{issue_id}-affects-{key}",
                        claim_type="AFFECTS_COMPONENT",
                        subject_id=issue_id,
                        object_id=f"component:{key}",
                        confidence=0.95,  # Labels are strong signals
                        validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                        extraction_version=self.extraction_version,
                        evidence=[component_evidence]
                    )
                    claims.append(affects_claim)
                    break
            
            # Create evidence for label
            label_evidence = Evidence(
                source_type="issue_label",
                source_id=f"issue:{issue_number}:label:{label['name']}",
                source_url=issue["html_url"],
                excerpt=f"Label: {label['name']}",
                timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
            )
            # Avoid duplicate evidence (already added for component)
            if not any(e.source_id == label_evidence.source_id for e in evidences):
                evidences.append(label_evidence)
            else:
                label_evidence = next(e for e in evidences if e.source_id == label_evidence.source_id)
            
            # Create label claim for all labels
            label_claim = Claim(
                id=f"claim:{issue_id}-has-label-{label['name']}",
                claim_type="HAS_LABEL",
                subject_id=issue_id,
                value={"label": label["name"], "color": label.get("color")},
                confidence=1.0,
                validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                extraction_version=self.extraction_version,
                evidence=[label_evidence]
            )
            claims.append(label_claim)
        
        # State claim with evidence
        state_evidence = Evidence(
            source_type="issue_metadata",
            source_id=f"issue:{issue_number}:state",
            source_url=issue["html_url"],
            excerpt=f"Issue state: {issue['state']}",
            timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
        )
        evidences.append(state_evidence)
        
        state_claim = Claim(
            id=f"claim:{issue_id}-state-{issue['state']}",
            claim_type="STATE",
            subject_id=issue_id,
            value={"state": issue["state"]},
            confidence=1.0,
            validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
            validity_end=None if issue["state"] == "open" else (
                datetime.fromisoformat(issue["closed_at"].replace("Z", "+00:00")) if issue.get("closed_at") else None
            ),
            extraction_version=self.extraction_version,
            evidence=[state_evidence]
        )
        claims.append(state_claim)
        
        return entities, claims, evidences
    
    def _extract_tier2_patterns(self, issue: dict) -> Tuple[List[Entity], List[Claim], List[Evidence]]:
        """
        Tier 2: Pattern-based extraction (no LLM needed)
        - @mentions, #issue refs, PR links, decisions
        """
        entities = []
        claims = []
        evidences = []
        
        issue_number = issue["number"]
        issue_id = f"issue:{issue_number}"
        
        # Combine body and comments
        text_sources = [(issue.get("body") or "", "body", issue["created_at"])]
        for comment in issue.get("comments_data", []):
            text_sources.append((
                comment.get("body") or "",
                f"comment:{comment['id']}",
                comment["created_at"]
            ))
        
        for text, source_type, timestamp in text_sources:
            if not text:
                continue
            
            # Extract @mentions
            mentions = re.findall(r"@([a-zA-Z0-9][-a-zA-Z0-9]*)", text)
            for mention in set(mentions):
                mention_entity = Entity(
                    id=f"person:{mention}",
                    type="Person",
                    canonical_name=mention,
                )
                entities.append(mention_entity)
                
                # Create evidence for the mention
                mention_match = re.search(rf".{{0,30}}@{re.escape(mention)}.{{0,30}}", text)
                mention_excerpt = mention_match.group() if mention_match else f"@{mention}"
                mention_evidence = Evidence(
                    source_type="mention",
                    source_id=f"{issue_id}:{source_type}:mention:{mention}",
                    excerpt=mention_excerpt,
                    timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                )
                evidences.append(mention_evidence)
                
                mention_claim = Claim(
                    id=f"claim:{issue_id}-mentions-{mention}",
                    claim_type="MENTIONS",
                    subject_id=issue_id,
                    object_id=f"person:{mention}",
                    confidence=0.9,
                    validity_start=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                    extraction_version=self.extraction_version,
                    evidence=[mention_evidence]
                )
                claims.append(mention_claim)
            
            # Extract issue references (#123)
            issue_refs = re.findall(r"#(\d+)", text)
            for ref in set(issue_refs):
                if int(ref) != issue_number:  # Don't self-reference
                    ref_entity = Entity(
                        id=f"issue:{ref}",
                        type="Issue",
                        canonical_name=f"Issue #{ref}",
                    )
                    entities.append(ref_entity)
                    
                    # Determine relationship type
                    lower_text = text.lower()
                    if "duplicate" in lower_text and f"#{ref}" in text:
                        claim_type = "DUPLICATES"
                        confidence = 0.85
                    elif "fixes" in lower_text or "closes" in lower_text:
                        claim_type = "FIXED_BY"
                        confidence = 0.9
                    elif "blocks" in lower_text:
                        claim_type = "BLOCKS"
                        confidence = 0.8
                    elif "blocked by" in lower_text:
                        claim_type = "BLOCKED_BY"
                        confidence = 0.8
                    else:
                        claim_type = "REFERENCES"
                        confidence = 0.7
                    
                    # Create evidence for the reference
                    ref_match = re.search(rf".{{0,30}}#{ref}.{{0,30}}", text)
                    ref_excerpt = ref_match.group() if ref_match else f"#{ref}"
                    ref_evidence = Evidence(
                        source_type="reference",
                        source_id=f"{issue_id}:{source_type}:ref:{ref}",
                        excerpt=ref_excerpt,
                        timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                    )
                    evidences.append(ref_evidence)
                    
                    ref_claim = Claim(
                        id=f"claim:{issue_id}-{claim_type.lower()}-{ref}",
                        claim_type=claim_type,
                        subject_id=issue_id,
                        object_id=f"issue:{ref}",
                        confidence=confidence,
                        validity_start=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                        extraction_version=self.extraction_version,
                        evidence=[ref_evidence]
                    )
                    claims.append(ref_claim)
            
            # Extract decision patterns
            lower_text = text.lower()
            for pattern in DECISION_PATTERNS:
                if pattern in lower_text:
                    # Extract evidence excerpt first
                    pattern_match = re.search(
                        rf".{{0,50}}{re.escape(pattern)}.{{0,50}}", 
                        text, 
                        re.IGNORECASE
                    )
                    decision_evidence = None
                    if pattern_match:
                        decision_evidence = Evidence(
                            source_type="decision_excerpt",
                            source_id=f"{issue_id}:{source_type}:decision:{pattern}",
                            excerpt=pattern_match.group(),
                            timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                        )
                        evidences.append(decision_evidence)
                    
                    decision_claim = Claim(
                        id=f"claim:{issue_id}-decision-{pattern.replace(' ', '-')}",
                        claim_type="DECISION",
                        subject_id=issue_id,
                        value={"decision": pattern, "source": source_type},
                        confidence=0.85,
                        validity_start=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                        extraction_version=self.extraction_version,
                        evidence=[decision_evidence] if decision_evidence else []
                    )
                    claims.append(decision_claim)
        
        return entities, claims, evidences
    
    def _extract_tier3_llm(self, issue: dict) -> Tuple[List[Entity], List[Claim], List[Evidence]]:
        """
        Tier 3: LLM-based extraction for complex understanding
        - Relationships, nuanced decisions, component inference
        """
        issue_number = issue["number"]
        issue_id = f"issue:{issue_number}"
        
        # Prepare prompt
        labels = ", ".join([l["name"] for l in issue.get("labels", [])])
        assignees = ", ".join([a["login"] for a in issue.get("assignees", [])])
        
        # Truncate body and comments for prompt
        body = (issue.get("body") or "")[:2000]
        comments = ""
        for i, comment in enumerate(issue.get("comments_data", [])[:5]):  # Max 5 comments
            comment_body = (comment.get("body") or "")[:500]
            comments += f"\n[Comment {i+1} by @{comment['user']['login']}]:\n{comment_body}\n"
        
        prompt = EXTRACTION_PROMPT.format(
            title=issue["title"],
            number=issue_number,
            state=issue["state"],
            labels=labels or "None",
            author=issue["user"]["login"],
            assignees=assignees or "None",
            created_at=issue["created_at"],
            closed_at=issue.get("closed_at") or "N/A",
            body=body or "No description provided",
            comments=comments or "No comments"
        )
        
        # Call LLM
        response = self._call_llm(prompt)
        extracted = self._parse_llm_response(response)
        
        # Check for parsing errors
        if "error" in extracted:
            raise ValueError(extracted["error"])
        
        entities = []
        claims = []
        evidences = []
        
        # Process extracted entities
        for entity_data in extracted.get("entities", []):
            entity = Entity(
                id=entity_data.get("id", f"unknown:{entity_data.get('name', 'unknown')}"),
                type=entity_data.get("type", "Unknown"),
                canonical_name=entity_data.get("name", "Unknown"),
            )
            entities.append(entity)
        
        # Process extracted claims
        for claim_data in extracted.get("claims", []):
            evidence_excerpt = claim_data.get("evidence_excerpt", "")
            
            # Create evidence from excerpt FIRST so we can link it to the claim
            claim_evidence = None
            if evidence_excerpt:
                claim_evidence = Evidence(
                    source_type="llm_extraction",
                    source_id=f"claim:{issue_id}-llm-{claim_data.get('type', 'unknown').lower()}-{len(claims)}:evidence",
                    source_url=issue["html_url"],
                    excerpt=evidence_excerpt,
                    timestamp=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                )
                evidences.append(claim_evidence)
            
            claim = Claim(
                id=f"claim:{issue_id}-llm-{claim_data.get('type', 'unknown').lower()}-{len(claims)}",
                claim_type=claim_data.get("type", "UNKNOWN"),
                subject_id=claim_data.get("subject", issue_id),
                object_id=claim_data.get("object"),
                value=claim_data.get("value"),
                confidence=claim_data.get("confidence", 0.7),
                validity_start=datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00")),
                extraction_version=self.extraction_version,
                evidence=[claim_evidence] if claim_evidence else []
            )
            claims.append(claim)
        
        return entities, claims, evidences
    
    def extract_from_issue(
        self, 
        issue: dict, 
        use_llm: bool = True
    ) -> Tuple[List[Entity], List[Claim], List[Evidence]]:
        """
        Extract all structured information from an issue
        
        Args:
            issue: GitHub issue dict from API
            use_llm: Whether to use Tier 3 LLM extraction
        
        Returns:
            Tuple of (entities, claims, evidences)
        """
        all_entities = []
        all_claims = []
        all_evidences = []
        
        # Tier 1: Structured
        entities, claims, evidences = self._extract_tier1_structured(issue)
        all_entities.extend(entities)
        all_claims.extend(claims)
        all_evidences.extend(evidences)
        
        # Tier 2: Patterns
        entities, claims, evidences = self._extract_tier2_patterns(issue)
        all_entities.extend(entities)
        all_claims.extend(claims)
        all_evidences.extend(evidences)
        
        # Tier 3: LLM (optional)
        if use_llm:
            try:
                entities, claims, evidences = self._extract_tier3_llm(issue)
                all_entities.extend(entities)
                all_claims.extend(claims)
                all_evidences.extend(evidences)
            except Exception as e:
                print(f"LLM extraction failed for issue #{issue['number']}: {e}")
        
        # Filter by confidence threshold
        all_claims = [c for c in all_claims if c.confidence >= CONFIDENCE_THRESHOLD]
        
        return all_entities, all_claims, all_evidences


def main():
    """Test extraction on a sample issue"""
    import json
    
    # Sample issue for testing
    sample_issue = {
        "number": 12345,
        "title": "Terminal crashes on startup",
        "state": "closed",
        "html_url": "https://github.com/microsoft/vscode/issues/12345",
        "created_at": "2025-01-15T10:00:00Z",
        "closed_at": "2025-02-20T14:30:00Z",
        "user": {"login": "testuser", "id": 12345, "html_url": "https://github.com/testuser"},
        "assignees": [{"login": "bpasero", "id": 900690, "html_url": "https://github.com/bpasero"}],
        "labels": [{"name": "bug", "color": "red"}, {"name": "terminal", "color": "blue"}],
        "body": "The terminal crashes when I open VS Code. @bpasero can you look at this? See also #11111.",
        "comments_data": [
            {
                "id": "abc123",
                "user": {"login": "bpasero"},
                "created_at": "2025-02-20T14:00:00Z",
                "body": "Fixed in #67890. The issue was a race condition in terminal initialization."
            }
        ],
        "events_data": []
    }
    
    extractor = Extractor()
    entities, claims, evidences = extractor.extract_from_issue(sample_issue, use_llm=False)
    
    print("=== Entities ===")
    for e in entities:
        print(f"  {e.id}: {e.type} - {e.canonical_name}")
    
    print("\n=== Claims ===")
    for c in claims:
        print(f"  {c.id}: {c.claim_type} ({c.confidence:.2f})")
    
    print("\n=== Evidences ===")
    for e in evidences:
        print(f"  {e.source_id}: {e.excerpt[:50] if e.excerpt else 'N/A'}...")


if __name__ == "__main__":
    main()
