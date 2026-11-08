"""Persistence layer: SQLAlchemy models, engine construction and Alembic migrations.

Restricted to the dialect-neutral subset so every feature works on SQLite and
PostgreSQL alike (ADR-001). Only the service layer may open a session or commit.
"""
