"""Test LLM extraction to debug JSON parsing."""
from groq import Groq
import os
import json
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv('GROQ_API_KEY'))

prompt = '''Extract entities and claims from this GitHub issue. Return ONLY valid JSON.

Title: Extension crashes on startup
Body: The terminal extension keeps crashing when I start VS Code. @bpasero can you help? This seems related to #12345.

Output format:
{"entities": [{"id": "...", "type": "...", "name": "..."}], "claims": [{"type": "...", "subject": "...", "object": "...", "confidence": 0.9, "evidence_excerpt": "..."}]}

Return ONLY the JSON, no markdown, no explanation.'''

response = client.chat.completions.create(
    model='llama-3.1-8b-instant',
    messages=[{'role': 'user', 'content': prompt}],
    temperature=0.1,
    max_tokens=1024
)

raw = response.choices[0].message.content
print('RAW RESPONSE:')
print(repr(raw))
print()
print('CLEANED:')
print(raw)
print()

# Try to parse
try:
    parsed = json.loads(raw)
    print('PARSED OK:', parsed)
except json.JSONDecodeError as e:
    print(f'JSON ERROR: {e}')
    
    # Try cleaning
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
        print('PARSED AFTER CLEANING:', parsed)
    except json.JSONDecodeError as e2:
        print(f'STILL FAILED: {e2}')
