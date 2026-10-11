"""
Plans, subscriptions, packs and question accounting (local SQLite, no external calls).
"""
from datetime import datetime, timedelta, timezone

import pytest

from soccer_bot.plans import FREE_TIER, load_catalog
from soccer_bot.store import Store

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture
def store(catalog):
    return Store(":memory:", catalog)


def ask(store, chat_id, n, at, exempt=False):
    return [store.reserve(chat_id, f"q{i}", exempt=exempt, now=at) for i in range(n)]


# --- catalog ---

def test_catalog_loads_pricing(catalog):
    assert catalog.plans["pro"].price == 14900 and catalog.plans["pro"].monthly_questions == 50
    assert catalog.plans["vip"].daily_cap == 15
    assert catalog.packs["recarga15"].questions == 15
    assert catalog.founder_offer.price == 9900


def test_command_gating(catalog):
    assert catalog.can_use(FREE_TIER, "forma")
    assert not catalog.can_use(FREE_TIER, "tarjetas")
    assert not catalog.can_use(FREE_TIER, "comparar")
    assert catalog.can_use("pro", "tarjetas")
    assert catalog.cheapest_plan_with("tarjetas").id == "pro"


# --- free tier ---

def test_free_gets_welcome_plus_weekly(store):
    results = ask(store, 1, 5, T0)
    assert [r.source for r in results[:4]] == ["free_welcome"] * 3 + ["free_weekly"]
    assert not results[4].allowed and results[4].reason == "free_exhausted"
    assert results[4].retry_at == T0 + timedelta(days=7)


def test_free_weekly_renews(store):
    ask(store, 1, 4, T0)
    assert not store.reserve(1, "q", now=T0 + timedelta(days=6)).allowed
    assert store.reserve(1, "q", now=T0 + timedelta(days=7, minutes=1)).source == "free_weekly"


def test_refund_returns_question(store):
    ids = [r.question_id for r in ask(store, 1, 4, T0)]
    store.refund(ids[-1])
    assert store.reserve(1, "q", now=T0).allowed


def test_admin_unlimited(store):
    assert all(r.source == "admin" for r in ask(store, 9, 20, T0, exempt=True))


# --- subscriptions ---

def test_pro_monthly_allowance(store):
    store.activate(1, "pro", now=T0)
    assert store.tier(1, now=T0) == "pro"
    results = ask(store, 1, 51, T0 + timedelta(hours=1))
    assert all(r.source == "subscription" for r in results[:50])
    assert results[50].reason == "plan_exhausted"


def test_subscription_expires(store):
    store.activate(1, "pro", now=T0)
    assert store.tier(1, now=T0 + timedelta(days=30, minutes=1)) == FREE_TIER


def test_vip_daily_cap(store):
    store.activate(1, "vip", now=T0)
    results = ask(store, 1, 16, T0)
    assert results[15].reason == "daily_cap"
    assert store.reserve(1, "q", now=T0 + timedelta(days=1, minutes=1)).source == "subscription"


def test_renewal_stacks_after_current_period(store):
    store.activate(1, "pro", now=T0)
    renewal = store.activate(1, "pro", now=T0 + timedelta(days=20))
    assert datetime.fromisoformat(renewal["ends_at_final"]) == T0 + timedelta(days=60)
    assert store.tier(1, now=T0 + timedelta(days=45)) == "pro"


def test_new_period_resets_allowance(store):
    store.activate(1, "pro", periods=2, now=T0)
    ask(store, 1, 50, T0)
    assert store.reserve(1, "q", now=T0 + timedelta(days=30, minutes=1)).source == "subscription"


def test_upgrade_replaces_plan(store):
    store.activate(1, "pro", now=T0)
    store.activate(1, "vip", now=T0 + timedelta(days=5))
    assert store.tier(1, now=T0 + timedelta(days=6)) == "vip"


def test_deactivate(store):
    store.activate(1, "pro", periods=3, now=T0)
    store.deactivate(1, now=T0 + timedelta(days=1))
    assert store.tier(1, now=T0 + timedelta(days=2)) == FREE_TIER
    assert store.tier(1, now=T0 + timedelta(days=40)) == FREE_TIER


def test_founder_price_and_slots(store):
    sub = store.activate(1, "pro", founder=True, now=T0)
    assert sub["price_paid"] == 9900 and sub["founder"] == 1
    store.activate(2, "pro", founder=True, now=T0)
    assert store.founder_slots_used() == 2


# --- packs ---

def test_pack_used_after_free_runs_out(store):
    ask(store, 1, 4, T0)
    store.add_pack(1, "recarga15", now=T0)
    results = ask(store, 1, 16, T0)
    assert all(r.source == "pack" for r in results[:15])
    assert not results[15].allowed


def test_pack_used_when_plan_exhausted(store):
    store.activate(1, "pro", now=T0)
    store.add_pack(1, "recarga15", now=T0)
    results = ask(store, 1, 52, T0)
    assert [r.source for r in results[49:52]] == ["subscription", "pack", "pack"]
    assert store.pack_balance(1) == 13


# --- reminders & reports ---

def test_reminder_once_and_not_if_renewed(store):
    store.activate(1, "pro", now=T0)
    store.activate(2, "pro", now=T0)
    store.activate(2, "pro", now=T0 + timedelta(days=1))  # renewed: no reminder
    due = store.due_for_reminder(3, now=T0 + timedelta(days=28))
    assert [d["chat_id"] for d in due] == [1]
    store.mark_reminded(due[0]["id"])
    assert store.due_for_reminder(3, now=T0 + timedelta(days=28)) == []


def test_account_view(store):
    ask(store, 1, 2, T0)
    info = store.account(1, now=T0)
    assert info["tier"] == FREE_TIER and info["welcome_left"] == 1 and info["weekly_left"] == 1
    store.activate(1, "vip", now=T0)
    info = store.account(1, now=T0 + timedelta(minutes=1))
    assert info["tier"] == "vip" and info["plan_remaining"] == 200 and info["daily_cap"] == 15


def test_metrics(store):
    store.touch_user(1, "ana", "Ana", now=T0)
    store.activate(1, "pro", now=T0)
    store.add_pack(2, "recarga15", now=T0)
    qid = store.reserve(1, "q", now=T0).question_id
    store.complete(qid, "answered", "forma", {"equipo": "Barça"}, input_tokens=1000, output_tokens=100)
    stats = store.usage_since(30, now=T0 + timedelta(days=1))
    assert stats["active_by_plan"] == {"pro": 1}
    assert stats["mrr"] == 14900 and stats["revenue"] == 14900 + 4900
    assert stats["cost_usd"] == pytest.approx(1000 * 4e-6 + 100 * 20e-6)
