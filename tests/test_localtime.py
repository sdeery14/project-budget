"""Derived dates follow the workspace time zone; typed dates are stored as written (#27)."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import csrf_token_from, log_in, make_client
from projectbudget import localtime
from projectbudget.domain.principal import Principal
from projectbudget.persistence.engine import make_engine
from projectbudget.persistence.models import SyncRun
from projectbudget.persistence.session import make_session_factory
from projectbudget.services import auth, clock, sync
from projectbudget.settings import Settings

# The OAT moment: 19:04 in New York on 2026-11-09 is 00:04 UTC on the 10th.
OAT_INSTANT = datetime(2026, 11, 10, 0, 4, tzinfo=UTC)


@pytest.fixture
def new_york() -> Iterator[None]:
    localtime.configure("America/New_York")
    try:
        yield
    finally:
        localtime.configure(None)


def test_an_instant_becomes_the_owners_date_not_the_utc_date(new_york: None) -> None:
    assert localtime.local_date(OAT_INSTANT) == date(2026, 11, 9)
    assert localtime.local_date(OAT_INSTANT.replace(tzinfo=None)) == date(2026, 11, 9)
    assert localtime.to_local(OAT_INSTANT).strftime("%Y-%m-%d %H:%M") == "2026-11-09 19:04"
    assert localtime.label() == "America/New_York"


def test_the_clock_and_the_zone_agree_on_today(new_york: None) -> None:
    assert clock.today() == localtime.today() == datetime.now(localtime.zone()).date()


def test_unset_falls_back_to_the_servers_zone() -> None:
    localtime.configure(None)
    assert localtime.label()
    assert isinstance(localtime.local_date(datetime.now(UTC)), date)


def test_settings_accept_an_iana_name_and_refuse_anything_else(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path, timezone="Europe/Amsterdam").timezone == "Europe/Amsterdam"
    assert Settings(data_dir=tmp_path, timezone=" ").timezone is None
    with pytest.raises(ValidationError, match="IANA"):
        Settings(data_dir=tmp_path, timezone="Mars/Olympus_Mons")
    with pytest.raises(localtime.UnknownTimezoneError):
        localtime.configure("EST5EDT/typo")


def test_pages_show_times_in_the_configured_zone_and_say_which(
    app_settings: Settings, owner: auth.OwnerInfo
) -> None:
    settings = Settings(
        database_url=app_settings.database_url,
        data_dir=app_settings.data_dir,
        timezone="America/New_York",
    )
    with make_client(settings) as client:
        log_in(client)
        tokens = client.get("/settings/tokens").text
        assert "Last used (America/New_York)" in tokens
        # A sync run recorded at 00:04 UTC on the 10th shows as the evening of the 9th.
        csrf = csrf_token_from(tokens)
        import_dir = settings.effective_sync_import_dir
        import_dir.mkdir(parents=True, exist_ok=True)
        (import_dir / "shed.yaml").write_text(
            "version: 1\nproject: {id: shed, name: Shed}\nitems: []\n", encoding="utf-8"
        )
        created = client.post(
            "/sync", data={"csrf_token": csrf, "kind": "file", "locator": "shed.yaml"}
        )
        source_id = created.headers["location"].rsplit("/", 1)[1]
    factory = make_session_factory(make_engine(settings.effective_database_url))
    principal = Principal(owner_id=owner.id, username=owner.username, auth="session")
    with factory.begin() as db:
        db.add(
            SyncRun(
                id="run-at-midnight-utc",
                source_id=source_id,
                status="applied",
                revision="abc",
                content_hash="x" * 64,
                summary="{}",
                created_at=OAT_INSTANT.replace(tzinfo=None),
            )
        )
    assert len(sync.list_runs(factory, principal, source_id)) == 1
    with make_client(settings) as client:
        log_in(client)
        page = client.get(f"/sync/{source_id}").text
    assert "When (America/New_York)" in page
    assert "2026-11-09 19:04" in page
    assert "2026-11-10 00:04" not in page
    assert "UTC" not in page.split('id="runs"')[1]
    localtime.configure(None)
