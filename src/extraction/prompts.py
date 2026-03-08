"""
Prompts for LLM-based extraction using Groq
"""

EXTRACTION_PROMPT = """You are an expert at extracting structured information from GitHub issues.

Given a GitHub issue, extract the following information in JSON format:

1. **Entities**: People, components, and related issues mentioned
2. **Claims**: Relationships and facts about the issue
3. **Decisions**: Any explicit decisions made (will fix, won't fix, duplicate, etc.)

## Entity Types:
- Person: GitHub users mentioned or involved
- Component: VS Code components/areas affected (terminal, editor, git, debugger, etc.)
- Issue: Related issue numbers referenced
- PullRequest: Related PR numbers referenced

## Claim Types:
- REPORTED_BY: Who reported the issue
- ASSIGNED_TO: Who is assigned to the issue
- AFFECTS_COMPONENT: Which component is affected
- FIXED_BY: PR that fixes this issue
- DUPLICATES: Issue this duplicates
- BLOCKS: Issues this blocks
- BLOCKED_BY: Issues blocking this
- MENTIONS: People mentioned in discussion
- DECISION: Explicit decisions about the issue

## Output Format:
Return ONLY valid JSON with this structure:
```json
{{
  "entities": [
    {{"id": "person:username", "type": "Person", "name": "Display Name"}},
    {{"id": "component:terminal", "type": "Component", "name": "Terminal"}}
  ],
  "claims": [
    {{
      "type": "AFFECTS_COMPONENT",
      "subject": "issue:NUMBER",
      "object": "component:NAME",
      "confidence": 0.9,
      "evidence_excerpt": "exact quote from issue"
    }},
    {{
      "type": "DECISION",
      "subject": "issue:NUMBER",
      "value": {{"decision": "won't fix", "reason": "by design"}},
      "confidence": 0.95,
      "evidence_excerpt": "exact quote"
    }}
  ]
}}
```

## Guidelines:
- confidence: 0.0-1.0 based on how explicitly stated the information is
- evidence_excerpt: Always include the exact text that supports the claim
- Only extract what is explicitly stated or strongly implied
- Components should match VS Code areas: terminal, editor, git, debugger, extensions, workbench, search, scm, tasks, etc.
- For DECISION claims, capture: "won't fix", "duplicate", "by design", "needs more info", "will fix", "fixed"

---

**Issue to analyze:**

Title: {title}
Number: #{number}
State: {state}
Labels: {labels}
Author: {author}
Assignees: {assignees}
Created: {created_at}
Closed: {closed_at}

**Body:**
{body}

**Comments:**
{comments}

---

Extract structured information from this issue. Return ONLY the JSON object, no other text.
"""

DECISION_PATTERNS = [
    "won't fix",
    "wontfix", 
    "by design",
    "as designed",
    "duplicate",
    "not a bug",
    "needs more info",
    "cannot reproduce",
    "will fix",
    "fixed in",
    "closing as",
    "this is expected",
]

COMPONENT_LABELS = {
    "terminal": "Terminal",
    "editor": "Editor",
    "editor-core": "Editor",
    "git": "Git/SCM",
    "scm": "Git/SCM",
    "debug": "Debugger",
    "debugger": "Debugger",
    "extensions": "Extensions",
    "extension-host": "Extensions",
    "workbench": "Workbench",
    "search": "Search",
    "tasks": "Tasks",
    "testing": "Testing",
    "notebooks": "Notebooks",
    "remote": "Remote",
    "settings": "Settings",
    "keybindings": "Keybindings",
    "themes": "Themes",
    "accessibility": "Accessibility",
    "languages": "Languages",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "python": "Python",
    "markdown": "Markdown",
    "json": "JSON",
    "html": "HTML",
    "css": "CSS",
}
