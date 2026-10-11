"""
Pricing catalog loaded from plans.json, so prices and quotas change without code changes.
"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "plans.json"
FREE_TIER = "free"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price: int
    days: int
    monthly_questions: int
    daily_cap: int | None
    commands: frozenset[str] | None  # None = all commands
    features: tuple[str, ...]


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    price: int
    questions: int


@dataclass(frozen=True)
class FounderOffer:
    plan: str
    price: int
    slots: int


@dataclass(frozen=True)
class Catalog:
    currency: str
    welcome_questions: int
    weekly_questions: int
    free_commands: frozenset[str]
    plans: dict[str, Plan]
    packs: dict[str, Pack]
    founder_offer: FounderOffer | None
    reminder_days_before: int

    def plan(self, plan_id: str) -> Plan | None:
        return self.plans.get(plan_id)

    def can_use(self, tier: str, command: str) -> bool:
        if tier == FREE_TIER:
            return command in self.free_commands
        plan = self.plans.get(tier)
        return plan is not None and (plan.commands is None or command in plan.commands)

    def cheapest_plan_with(self, command: str) -> Plan | None:
        eligible = [p for p in self.plans.values() if p.commands is None or command in p.commands]
        return min(eligible, key=lambda p: p.price, default=None)


def load_catalog(path: str | Path = DEFAULT_PATH) -> Catalog:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    plans = {
        p["id"]: Plan(
            id=p["id"], name=p["name"], price=int(p["price"]), days=int(p["days"]),
            monthly_questions=int(p["monthly_questions"]), daily_cap=p.get("daily_cap"),
            commands=None if p["commands"] == "all" else frozenset(p["commands"]),
            features=tuple(p.get("features", ())),
        )
        for p in raw["plans"]
    }
    if FREE_TIER in plans:
        raise ValueError(f"'{FREE_TIER}' is reserved and cannot be a plan id")
    founder = raw.get("founder_offer")
    if founder and founder["plan"] not in plans:
        raise ValueError(f"founder_offer references unknown plan: {founder['plan']}")
    return Catalog(
        currency=raw.get("currency", "COP"),
        welcome_questions=int(raw["free"]["welcome_questions"]),
        weekly_questions=int(raw["free"]["weekly_questions"]),
        free_commands=frozenset(raw["free"]["commands"]),
        plans=plans,
        packs={k["id"]: Pack(k["id"], k["name"], int(k["price"]), int(k["questions"])) for k in raw.get("packs", [])},
        founder_offer=FounderOffer(founder["plan"], int(founder["price"]), int(founder["slots"])) if founder else None,
        reminder_days_before=int(raw.get("reminder_days_before", 3)),
    )
