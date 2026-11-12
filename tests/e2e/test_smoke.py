"""The owner's first ten minutes, in a real browser (Owner Acceptance Testing, issue #11).

Every assertion here is something a person saw or should have seen during OAT. Keep the
journey short: sign in, create a project, add a requirement, price it, read the backlog,
and make sure a destructive button asks first. Layout checks run at 320 px as well.
"""

import re

import pytest
from playwright.sync_api import ConsoleMessage, Dialog, Page, expect

from .conftest import PHONE_WIDTH, LiveServer

PAGES = ["/backlog", "/timeline", "/budget", "/projects", "/catalog", "/purchases", "/sync"]


def _console_errors(page: Page) -> list[str]:
    seen: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if message.type == "error":
            seen.append(message.text)

    page.on("console", record)
    return seen


def test_wrong_password_is_refused_and_root_needs_a_session(
    page: Page, live_server: LiveServer
) -> None:
    # Signed out, every page answers 401 with the sign-in form in place (no redirect).
    response = page.goto(f"{live_server.url}/backlog")
    assert response is not None
    assert response.status == 401
    expect(page.get_by_role("heading", level=1)).to_have_text("Sign in")
    page.get_by_label("Username").fill(live_server.username)
    page.get_by_label("Password").fill("not the password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_contain_text("Wrong username or password")


def test_core_journey_in_the_browser(signed_in: Page, live_server: LiveServer) -> None:
    page = signed_in
    errors = _console_errors(page)

    page.goto(f"{live_server.url}/projects")
    page.get_by_label("New project").fill("Workbench")
    page.get_by_role("button", name="Create project").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("Workbench")

    page.get_by_label("Item", exact=True).fill("Bar clamp")
    page.get_by_label("Qty").fill("3")
    page.get_by_label("Priority").select_option("1")
    page.get_by_role("button", name="Add", exact=True).click()
    row = page.get_by_role("row", name=re.compile("Bar clamp"))
    expect(row).to_contain_text("3 each")
    expect(row).to_contain_text("P1")

    # Unpriced: counted, never summed.
    page.goto(f"{live_server.url}/backlog")
    expect(page.get_by_text("Needs a price", exact=False).first).to_be_visible()
    expect(page.get_by_role("link", name="Bar clamp")).to_be_visible()

    # Price it from the item page, then the backlog sums it.
    page.get_by_role("link", name="Bar clamp").click()
    page.get_by_label("Unit price (USD)").fill("12.50")
    page.get_by_label("As of").fill("2026-11-01")
    page.get_by_role("button", name="Add estimate").click()
    expect(page.get_by_text("12.50 USD")).to_be_visible()

    page.goto(f"{live_server.url}/backlog")
    expect(page.get_by_text("37.50 USD")).to_be_visible()
    expect(page.get_by_role("row", name=re.compile("Bar clamp"))).to_contain_text("12.50")
    assert errors == []


def test_merge_asks_before_submitting_and_names_both_items(
    signed_in: Page, live_server: LiveServer
) -> None:
    page = signed_in
    page.goto(f"{live_server.url}/catalog")
    for name in ("Clamp A", "Clamp B"):
        page.get_by_label("New item").fill(name)
        page.get_by_role("button", name="Create item").click()
        expect(page.get_by_role("heading", level=1)).to_contain_text(name)
        page.goto(f"{live_server.url}/catalog")
    page.get_by_role("link", name="Clamp A").click()

    # Nothing preselected: the browser refuses to submit an empty required select.
    page.get_by_role("button", name="Merge").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Clamp A")

    dialogs: list[str] = []

    def dismiss(dialog: Dialog) -> None:
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", dismiss)
    page.get_by_label("Keep this one instead").select_option(label="Clamp B (each, consumable)")
    page.get_by_role("button", name="Merge").click()
    assert len(dialogs) == 1
    assert "Merge “Clamp A” into “Clamp B" in dialogs[0]
    assert "cannot be undone" in dialogs[0]
    # Dismissed: still here, still unmerged.
    page.goto(f"{live_server.url}/catalog")
    expect(page.get_by_role("link", name="Clamp A")).to_be_visible()


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_without_console_errors(
    signed_in: Page, live_server: LiveServer, path: str
) -> None:
    errors = _console_errors(signed_in)
    response = signed_in.goto(f"{live_server.url}{path}")
    assert response is not None
    assert response.ok
    expect(signed_in.get_by_role("heading", level=1)).to_be_visible()
    assert errors == []


@pytest.mark.parametrize("path", PAGES)
def test_pages_fit_a_phone_without_sideways_scrolling(
    phone: Page, live_server: LiveServer, path: str
) -> None:
    phone.goto(f"{live_server.url}{path}")
    width = phone.evaluate("() => document.documentElement.scrollWidth")
    assert width <= PHONE_WIDTH, f"{path} is {width}px wide at {PHONE_WIDTH}px"


def test_backlog_is_a_real_table_on_a_desktop(signed_in: Page, live_server: LiveServer) -> None:
    """The phone rules must not leak upward: header and body columns line up at 1280 px (#15)."""
    signed_in.goto(f"{live_server.url}/backlog")
    widths = signed_in.evaluate(
        """() => {
            const t = document.querySelector('#backlog-rows');
            const w = cells => [...cells].map(c => Math.round(c.getBoundingClientRect().width));
            return {display: getComputedStyle(t).display,
                    head: w(t.tHead.rows[0].cells), body: w(t.tBodies[0].rows[0].cells)};
        }"""
    )
    assert widths["display"] == "table"
    assert widths["head"] == widths["body"]


def test_backlog_rows_stack_on_a_phone(phone: Page, live_server: LiveServer) -> None:
    """The columns that answer the question stay on screen instead of scrolling away (#15)."""
    phone.goto(f"{live_server.url}/backlog")
    row = phone.locator("#backlog-rows tbody tr").first
    box = row.bounding_box()
    assert box is not None
    assert box["x"] + box["width"] <= PHONE_WIDTH
    for label in ("Shortfall", "Unit price", "Line total"):
        cell = row.locator(f'td[data-label="{label}"]')
        expect(cell).to_be_visible()
        cell_box = cell.bounding_box()
        assert cell_box is not None, label
        assert cell_box["x"] + cell_box["width"] <= PHONE_WIDTH, label


def test_an_open_order_moves_money_from_still_to_buy_to_committed(
    signed_in: Page, live_server: LiveServer
) -> None:
    """ADR-012 (#25): the backlog's money agrees with the timeline once something is ordered."""
    page = signed_in
    errors = _console_errors(page)
    page.goto(f"{live_server.url}/purchases")
    page.get_by_label("First item").select_option(label="Bar clamp (each)")
    page.get_by_label("Qty").fill("1")
    page.get_by_label("Unit price (USD)").fill("12.00")
    page.get_by_role("button", name="Record purchase").click()
    # The page is headed by what was bought, not by the word "Purchase" (#23).
    expect(page.get_by_role("heading", level=1)).to_contain_text("Bar clamp")

    page.goto(f"{live_server.url}/backlog")
    expect(page.locator("#backlog-total")).to_have_text("25.00 USD")  # 3 short, 1 on order
    expect(page.locator("#backlog-committed")).to_have_text("12.00 USD")  # at the order's price
    row = page.get_by_role("row", name=re.compile("Bar clamp"))
    expect(row.locator('td[data-label="On order"]')).to_have_text("1")
    expect(row.locator('td[data-label="Line total"]')).to_have_text("25.00")
    assert errors == []


def test_a_sync_source_page_fits_a_phone(signed_in: Page, live_server: LiveServer) -> None:
    """The source page (settings, plan, runs) stacks at phone width like the other pages."""
    page = signed_in
    live_server.import_dir.mkdir(parents=True, exist_ok=True)
    manifest = "\n".join(
        [
            "version: 1",
            "project: {id: shed, name: Shed}",
            "items:",
            "  - {id: saw, name: Hand saw, qty: 1, kind: reusable}",
            "",
        ]
    )
    (live_server.import_dir / "shed.yaml").write_text(manifest, encoding="utf-8")
    page.goto(f"{live_server.url}/sync")
    page.get_by_label("Source", exact=True).select_option("file")
    page.get_by_label("File path or owner/repo").fill("shed.yaml")
    page.get_by_role("button", name="Add source").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("shed.yaml")
    page.get_by_role("button", name="Fetch and preview").click()
    expect(page.locator("#plan")).to_be_visible()

    page.set_viewport_size({"width": PHONE_WIDTH, "height": 700})
    for path in (page.url, f"{live_server.url}/sync"):
        page.goto(path)
        width = page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= PHONE_WIDTH, f"{path} is {width}px wide at {PHONE_WIDTH}px"


def test_tokens_item_and_budget_tables_fit_a_phone(
    signed_in: Page, live_server: LiveServer
) -> None:
    """The tokens, estimates, movements and overrides tables stack at phone width (release pass)."""
    page = signed_in
    errors = _console_errors(page)

    page.goto(f"{live_server.url}/settings/tokens")
    page.get_by_label("Name").fill("phone check")
    page.get_by_role("button", name="Create token").click()
    expect(page.locator("#tokens")).to_contain_text("phone check")

    page.goto(f"{live_server.url}/catalog")
    page.get_by_label("New item").fill("Phone-width widget")
    page.get_by_role("button", name="Create item").click()
    page.goto(f"{live_server.url}/catalog")
    page.get_by_role("link", name="Phone-width widget").click()
    page.get_by_label(re.compile(r"^Unit price")).fill("12.50")
    page.get_by_role("button", name="Add estimate").click()
    page.get_by_label("Adjust by").fill("2")
    page.get_by_label("Reason").fill("stock take, counted on the shelf behind the bench")
    page.get_by_role("button", name="Record adjustment").click()
    item_url = page.url
    expect(page.locator("#estimates")).to_contain_text("12.50")
    expect(page.locator("#movements")).to_contain_text("+2")

    page.goto(f"{live_server.url}/budget")
    create = page.get_by_role("button", name="Create plan")
    if create.is_visible():
        page.get_by_label(re.compile(r"^Balance \(")).fill("100")
        create.click()
    page.get_by_label("Month", exact=True).fill("2027-01")
    page.get_by_label(re.compile(r"^Amount")).fill("50")
    page.get_by_label("Label", exact=True).fill("Birthday money from the grandparents")
    page.get_by_role("button", name="Add override").click()
    expect(page.locator("#overrides")).to_contain_text("Birthday money")

    page.set_viewport_size({"width": PHONE_WIDTH, "height": 700})
    for path, table in (
        (f"{live_server.url}/settings/tokens", "#tokens"),
        (item_url, "#estimates"),
        (item_url, "#movements"),
        (f"{live_server.url}/budget", "#overrides"),
    ):
        page.goto(path)
        width = page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= PHONE_WIDTH, f"{path} is {width}px wide at {PHONE_WIDTH}px"
        cell = page.locator(f"{table} tbody td[data-label]").first
        expect(cell).to_be_visible()
        box = cell.bounding_box()
        assert box is not None, table
        assert box["x"] + box["width"] <= PHONE_WIDTH, table
    assert errors == []
