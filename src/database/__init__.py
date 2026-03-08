"""
Database module - SQLite schema and operations
"""
from .schema import init_database, get_connection
from .models import Entity, Claim, Evidence, Alias, MergeRecord

__all__ = ["init_database", "get_connection", "Entity", "Claim", "Evidence", "Alias", "MergeRecord"]
