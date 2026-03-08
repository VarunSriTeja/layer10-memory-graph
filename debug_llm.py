"""Debug LLM extraction on real issue."""
import json
from groq import Groq
import os
from dotenv import load_dotenv
load_dotenv()

from src.extraction.prompts import EXTRACTION_PROMPT

# Load first issue
with open('data/raw/vscode_issues_20260308_164724.json') as f:
    data = json.load(f)

issue = data['issues'][0]
print(f"Testing issue #{issue['number']}: {issue['title'][:50]}...")

# Build prompt
labels = ", ".join([l["name"] for l in issue.get("labels", [])])
assignees = ", ".join([a["login"] for a in issue.get("assignees", [])])
body = (issue.get("body") or "")[:2000]
comments = ""
for i, comment in enumerate(issue.get("comments_data", [])[:5]):
    comment_body = (comment.get("body") or "")[:500]
    comments += f"\n[Comment {i+1} by @{comment['user']['login']}]:\n{comment_body}\n"

prompt = EXTRACTION_PROMPT.format(
    title=issue["title"],
    number=issue["number"],
    state=issue["state"],
    labels=labels or "None",
    author=issue["user"]["login"],
    assignees=assignees or "None",
    created_at=issue["created_at"],
    closed_at=issue.get("closed_at") or "N/A",
    body=body or "No description provided",
    comments=comments or "No comments"
)

print("\n=== PROMPT LENGTH ===")
print(f"{len(prompt)} characters")

print("\n=== CALLING GROQ ===")
client = Groq(api_key=os.getenv('GROQ_API_KEY'))
response = client.chat.completions.create(
    model='llama-3.1-8b-instant',
    messages=[
        {"role": "system", "content": "You are a precise JSON extraction assistant. Always return valid JSON."},
        {"role": "user", "content": prompt}
    ],
    temperature=0.1,
    max_tokens=2048,
)

raw = response.choices[0].message.content
print("\n=== RAW RESPONSE (first 500 chars) ===")
print(repr(raw[:500]))

print("\n=== TRY PARSING ===")
try:
    parsed = json.loads(raw)
    print("SUCCESS:", json.dumps(parsed, indent=2)[:500])
except json.JSONDecodeError as e:
    print(f"FAILED: {e}")
    print("\n=== TRYING CLEANUP ===")
    import re
    if '```json' in raw:
        match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
        if match:
            raw = match.group(1)
    elif '```' in raw:
        match = re.search(r'```\s*(.*?)\s*```', raw, re.DOTALL)
        if match:
            raw = match.group(1)
    
    try:
        parsed = json.loads(raw.strip())
        print("SUCCESS AFTER CLEANUP:", json.dumps(parsed, indent=2)[:500])
    except json.JSONDecodeError as e2:
        print(f"STILL FAILED: {e2}")
        print("RAW after cleanup:", repr(raw[:200]))
