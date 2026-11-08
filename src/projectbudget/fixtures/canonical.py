"""The canonical fictional workspace (RFC v3 §2, acceptance tests 1 and 2).

Two projects share one reusable item (the workbench needs 3 bar clamps, the
garden gate needs 2) and one consumable (10 and 5 carriage bolts). One clamp
and four bolts are on hand, so the backlog must show a shortfall of 2 clamps
and 11 bolts. The gate's hinge set has no price and lands in the "needs a
price" bucket; the workbench vise is an exclusive reusable item. Every number
here is made up and in USD.

Built through the service layer so it exercises exactly what users do.
"""

from dataclasses import dataclass
from datetime import date, datetime

from projectbudget.domain.catalog import Confidence, ItemKind, Sharing
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import budget, catalog, inventory, projects, requirements, workspace
from projectbudget.services.budget import PlanInfo
from projectbudget.services.catalog import CatalogItemInfo
from projectbudget.services.projects import ProjectInfo
from projectbudget.services.requirements import RequirementInfo

CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class CanonicalWorkspace:
    workbench: ProjectInfo
    garden_gate: ProjectInfo
    clamp: CatalogItemInfo
    bolts: CatalogItemInfo
    hinge_set: CatalogItemInfo
    vise: CatalogItemInfo
    workbench_clamps: RequirementInfo
    gate_clamps: RequirementInfo
    workbench_bolts: RequirementInfo
    gate_bolts: RequirementInfo
    gate_hinges: RequirementInfo
    workbench_vise: RequirementInfo
    plan: PlanInfo


def seed_canonical(
    session_factory: SessionFactory,
    principal: Principal,
    *,
    now: datetime | None = None,
    as_of: date | None = None,
) -> CanonicalWorkspace:
    """Create the canonical workspace. The database must be empty of projects and items."""
    workspace.set_currency(session_factory, CURRENCY, now=now)

    workbench = projects.create_project(
        session_factory,
        principal,
        projects.ProjectCreate(name="Workbench", notes="A sturdy bench for the garage."),
        now=now,
    )
    gate = projects.create_project(
        session_factory,
        principal,
        projects.ProjectCreate(name="Garden gate", notes="Replace the rotten side gate."),
        now=now,
    )

    clamp = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(
            name="Bar clamp 600 mm", slug="bar-clamp-600", kind=ItemKind.REUSABLE
        ),
        now=now,
    )
    bolts = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="M8 carriage bolt 80 mm", slug="m8-carriage-bolt-80"),
        now=now,
    )
    hinge_set = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="Gate hinge set", slug="gate-hinge-set", unit="set"),
        now=now,
    )
    vise = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(
            name="Bench vise 125 mm", slug="bench-vise-125", kind=ItemKind.REUSABLE
        ),
        now=now,
    )

    def need(
        project: ProjectInfo,
        item: CatalogItemInfo,
        qty: int,
        *,
        priority: int = 2,
        sharing: Sharing = Sharing.SHARED,
    ) -> RequirementInfo:
        return requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(
                project_id=project.id,
                qty=qty,
                priority=priority,
                catalog_item_id=item.id,
                sharing=sharing,
            ),
            now=now,
        )

    workbench_clamps = need(workbench, clamp, 3)
    gate_clamps = need(gate, clamp, 2)
    workbench_bolts = need(workbench, bolts, 10, priority=3)
    gate_bolts = need(gate, bolts, 5, priority=3)
    gate_hinges = need(gate, hinge_set, 1, priority=1)
    workbench_vise = need(workbench, vise, 1, sharing=Sharing.EXCLUSIVE)

    inventory.adjust(
        session_factory, principal, clamp.id, 1, occurred_on=as_of, note="Stock take", now=now
    )
    inventory.adjust(
        session_factory, principal, bolts.id, 4, occurred_on=as_of, note="Stock take", now=now
    )

    def price(item: CatalogItemInfo, text: str, confidence: Confidence) -> None:
        catalog.add_estimate(
            session_factory,
            principal,
            item.id,
            catalog.EstimateCreate(
                price=Money.parse(text, CURRENCY), as_of=as_of, confidence=confidence
            ),
            now=now,
        )

    price(clamp, "12.50", Confidence.HIGH)
    price(bolts, "0.45", Confidence.MEDIUM)
    price(vise, "89.00", Confidence.LOW)

    # 150.00 in the jar on as_of, 80.00 of it protected, 25.00 added each month, 10 % contingency:
    # the clamps fit in month 1, the vise waits for month 2 (optimistic) or month 3 (conservative).
    plan = budget.set_plan(
        session_factory,
        principal,
        budget.PlanUpdate(
            earmarked_funds=Money.parse("150.00", CURRENCY),
            protected_reserve=Money.parse("80.00", CURRENCY),
            monthly_contribution=Money.parse("25.00", CURRENCY),
            horizon_months=12,
            contingency_pct=10,
            balance_as_of=as_of,
        ),
        now=now,
    )

    return CanonicalWorkspace(
        workbench=workbench,
        garden_gate=gate,
        clamp=clamp,
        bolts=bolts,
        hinge_set=hinge_set,
        vise=vise,
        workbench_clamps=workbench_clamps,
        gate_clamps=gate_clamps,
        workbench_bolts=workbench_bolts,
        gate_bolts=gate_bolts,
        gate_hinges=gate_hinges,
        workbench_vise=workbench_vise,
        plan=plan,
    )
