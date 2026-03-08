"""
Extraction module - LLM-based structured extraction
"""
from .extractor import Extractor
from .prompts import EXTRACTION_PROMPT

__all__ = ["Extractor", "EXTRACTION_PROMPT"]
