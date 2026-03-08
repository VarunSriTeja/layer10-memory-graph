"""
Configuration settings for Layer10 Memory Graph
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "memory.db"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# Create directories
for dir_path in [DATA_DIR, RAW_DATA_DIR, PROCESSED_DATA_DIR, OUTPUTS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# GitHub settings
GITHUB_REPO = "microsoft/vscode"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Optional, increases rate limit
ISSUES_TO_FETCH = 2000  # Start with 2000 recent issues

# Groq settings
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.1-8b-instant"  # Fast, free tier compatible
GROQ_MAX_TOKENS = 2048
GROQ_TEMPERATURE = 0.1  # Low temp for structured extraction

# Embedding settings
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384

# Extraction settings
EXTRACTION_BATCH_SIZE = 10  # Issues per batch
CONFIDENCE_THRESHOLD = 0.5  # Minimum confidence to store claim
MAX_RETRIES = 3

# Deduplication settings
SIMILARITY_THRESHOLD = 0.85  # For near-duplicate detection
HASH_ALGORITHM = "sha256"

# Retrieval settings
MAX_RESULTS = 20
MAX_HOPS = 2
TOP_K_PER_HOP = 10
