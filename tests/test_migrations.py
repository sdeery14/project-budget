"""Migration round-trip on both backends (RFC v3 §8: Migrations row)."""

from projectbudget.persistence import migrate


def test_upgrade_from_empty_matches_models_and_downgrades_one_step(database_url: str) -> None:
    migrate.downgrade(database_url, "base")
    assert migrate.current_revision(database_url) is None

    migrate.upgrade(database_url, "head")
    assert migrate.current_revision(database_url) == migrate.head_revision()

    # Autogenerate must produce no diff: the migrations fully describe the models.
    assert migrate.pending_model_changes(database_url) == []

    migrate.downgrade(database_url, "-1")
    assert migrate.current_revision(database_url) is None


def test_head_revision_is_sequential() -> None:
    head = migrate.head_revision()
    assert head is not None
    assert head.isdigit()
    assert len(head) == 4


def test_percent_in_url_survives_configparser() -> None:
    cfg = migrate.alembic_config("postgresql+psycopg://u:p%40ss@h/db")
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql+psycopg://u:p%40ss@h/db"
