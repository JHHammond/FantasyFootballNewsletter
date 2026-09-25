"""
Stripe.

Three things happen here and nothing else: send somebody to Checkout, listen to
what Stripe says afterwards, and send them to the billing portal to cancel.
Deciding what a plan ALLOWS is plans.py; this file only ever answers "has this
person paid".

THE WEBHOOK IS THE ONLY THING THAT GRANTS ACCESS.

Not the success redirect. A browser coming back to /billing/done proves
somebody visited a URL — it is a query string, and anybody can type one. The
webhook is signed with a secret only Stripe and this server know, and it is
the sole path by which `plan` is ever set to paid. The redirect page says
"thanks" and re-reads the account; that is all it does.

This costs a few seconds of latency in the rare case where the webhook lands
after the redirect, which is why the return page says the subscription may
take a moment. That is the correct trade: the alternative is a paywall that
anybody can walk through by editing a URL.

VERIFIED AGAINST THE INSTALLED LIBRARY, not from memory (stripe 15.x, API
version 2026-08-26):

  - `current_period_end` is NOT on the Subscription any more. It moved to the
    subscription ITEM in the 2025-03-31 API version, and reading it off the
    subscription now returns nothing at all — silently, which is the worst
    kind of nothing.
  - Subscription.status is one of: active, trialing, past_due, canceled,
    unpaid, incomplete, incomplete_expired, paused.

UNCONFIGURED IS A SUPPORTED STATE. No STRIPE_SECRET_KEY means no upgrade
buttons and a webhook route that 404s. The gates still hold — everyone is on
the free plan — so a half-configured deploy gives nothing away.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import plans


class BillingError(Exception):
    """Something went wrong talking to Stripe. Never contains a key."""


class NotConfigured(BillingError):
    """This deployment has no Stripe credentials."""


def _stripe():
    """The SDK, configured, or NotConfigured.

    Imported here rather than at module scope so the app still boots — and the
    whole test suite still runs — on a machine that has never installed it.
    """
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        raise NotConfigured("STRIPE_SECRET_KEY is not set")
    try:
        import stripe as stripe_sdk
    except ImportError as exc:  # pragma: no cover - deployment problem
        raise NotConfigured("the stripe package is not installed") from exc
    stripe_sdk.api_key = key
    return stripe_sdk


#: Which environment variable holds the Stripe price for each term.
_PRICE_ENV = {plans.MONTHLY: "STRIPE_PRICE_ID",
              plans.SEASON: "STRIPE_SEASON_PRICE_ID"}


def price_id(term: str = plans.MONTHLY) -> str:
    name = _PRICE_ENV.get(term)
    if not name:
        raise BillingError(f"unknown term {term!r}")
    value = os.getenv(name)
    if not value:
        raise NotConfigured(f"{name} is not set")
    return value


def webhook_secret() -> str:
    return os.getenv("STRIPE_WEBHOOK_SECRET") or ""


def configured() -> bool:
    return plans.billing_enabled()


# ---------------------------------------------------------------------------
# Going to Stripe
# ---------------------------------------------------------------------------

def _customer_for(db, user: dict[str, Any]) -> str:
    """This account's Stripe customer, created once and reused forever.

    Reused deliberately. Somebody who subscribes, cancels in March and comes
    back in September should have one customer with one billing history, not
    two customers and a support question about which card is on file.

    The id is written to our database BEFORE checkout opens, because the
    webhook identifies people by customer id and it has to be able to find
    them even if the person closes the Stripe tab and never comes back to the
    success URL.
    """
    existing = (user.get("stripe_customer_id") or "").strip()
    if existing:
        return existing

    customer = _stripe().Customer.create(
        email=user.get("email") or None,
        # So a human looking at the Stripe dashboard can tell who this is
        # without a database query.
        metadata={"user_id": str(user["id"])},
    )
    db.remember_stripe_customer(user["id"], customer.id)
    return customer.id


def _is_missing_customer(exc) -> bool:
    """Stripe's "No such customer" — resource_missing, on the customer param."""
    return (getattr(exc, "code", None) == "resource_missing"
            and getattr(exc, "param", None) == "customer")


def checkout_url(db, user: dict[str, Any], base_url: str,
                 term: str = plans.MONTHLY) -> str:
    """Where to send somebody who wants to pay.

    The term only picks the price. The webhook never asks which one was
    bought: an active subscription is the paid plan either way.
    """
    price = price_id(term)
    stripe_sdk = _stripe()
    base = (base_url or "").rstrip("/")

    def open_session(customer: str):
        return stripe_sdk.checkout.Session.create(
            mode="subscription",
            customer=customer,
            line_items=[{"price": price, "quantity": 1}],
            success_url=f"{base}/billing/done?ok=1",
            cancel_url=f"{base}/account?notice=No+charge+was+made.",
            # Both, on purpose. client_reference_id is the documented way to
            # tie a session back to your own user and rides on the session;
            # the metadata copy is carried onto the SUBSCRIPTION, which is
            # what later events are about. Either one alone leaves a gap.
            client_reference_id=str(user["id"]),
            subscription_data={"metadata": {"user_id": str(user["id"])}},
            allow_promotion_codes=True,
        )

    try:
        try:
            session = open_session(_customer_for(db, user))
        except stripe_sdk.InvalidRequestError as exc:
            if not _is_missing_customer(exc):
                raise
            # The stored customer doesn't exist for THIS key. Almost always
            # a switch between live and test keys: customers made under one
            # are invisible to the other. Nothing is lost by making a fresh
            # one — no subscription can be active on a customer Stripe says
            # doesn't exist here — and the alternative is an account that can
            # never check out again until somebody edits the database.
            print(f"[billing] stored customer for {user['id']} not found "
                  f"under this key ({exc.user_message or exc}); making a "
                  f"new one", flush=True)
            fresh = {**user, "stripe_customer_id": None}
            session = open_session(_customer_for(db, fresh))
    except stripe_sdk.StripeError as exc:
        raise BillingError(f"{type(exc).__name__}: {exc}") from exc

    if not session.url:
        raise BillingError("Stripe created a session with no URL")
    return session.url


def portal_url(db, user: dict[str, Any], base_url: str) -> str:
    """Stripe's own page for changing a card or cancelling.

    Cancelling lives there rather than here, deliberately. Stripe handles
    proration, the "you keep it until the 14th" arithmetic and the receipt,
    and every one of those is a thing to get wrong by hand.
    """
    customer = (user.get("stripe_customer_id") or "").strip()
    if not customer:
        raise BillingError("no Stripe customer for this account")

    base = (base_url or "").rstrip("/")
    stripe_sdk = _stripe()
    try:
        session = stripe_sdk.billing_portal.Session.create(
            customer=customer,
            return_url=f"{base}/account",
        )
    except stripe_sdk.StripeError as exc:
        raise BillingError(f"{type(exc).__name__}: {exc}") from exc
    return session.url


# ---------------------------------------------------------------------------
# Hearing back from Stripe
# ---------------------------------------------------------------------------

#: The events worth acting on. Everything else Stripe sends is acknowledged
#: and ignored — an endpoint that errors on an event it does not care about
#: teaches Stripe to retry it forever.
HANDLED_EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})


def verify(payload: bytes, signature: str):
    """Stripe's event, or an exception.

    The signature check is the entire security of this endpoint. The URL is
    public and unguessable-but-not-secret, and the body is JSON anybody can
    write; without this, a stranger who guesses the path can hand themselves a
    subscription by POSTing a made-up event.
    """
    secret = webhook_secret()
    if not secret:
        raise NotConfigured("STRIPE_WEBHOOK_SECRET is not set")

    stripe_sdk = _stripe()
    try:
        return stripe_sdk.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:  # noqa: BLE001 — SDK raises several types
        raise BillingError(f"rejected: {type(exc).__name__}") from exc


def _field(obj, name, default=None):
    """One field off a Stripe object.

    Stripe's own objects support attribute AND item access, so `getattr` alone
    works against the real SDK. It stops working the moment anything hands
    this a plain dict — a replayed payload, a fixture, a future refactor — and
    it stops working SILENTLY, returning None and quietly deciding nobody
    matched. A webhook that no-ops on a shape it did not expect is the exact
    failure that loses a paying customer without anybody noticing.
    """
    # A plain dict first, by key. getattr on a dict finds its METHODS: a
    # subscription's "items" field comes back as dict.items, the period end
    # reads as missing, and the renewal date is silently never stored.
    if isinstance(obj, dict):
        value = obj.get(name)
        return default if value is None else value

    value = getattr(obj, name, None)
    if value is None and hasattr(obj, "get"):
        try:
            value = obj.get(name)
        except Exception:  # noqa: BLE001
            value = None
    return default if value is None else value


def _as_dict(value) -> dict:
    """A Stripe object (or a plain dict) as a plain dict, or {}.

    stripe-python 12+ StripeObjects are NOT dicts any more: `.get()` raises
    AttributeError and `hasattr(obj, "get")` is False (24 Sep). Reading
    metadata that way silently found nothing, so a payment that had to be
    matched through its metadata was dropped as "no matching account".
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict() or {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _period_end(subscription) -> Optional[str]:
    """When the current paid period runs out, as an ISO timestamp.

    On the subscription ITEM, not the subscription. Stripe moved it in the
    2025-03-31 API version and left nothing behind on the parent, so the
    obvious `subscription.current_period_end` reads as None on every modern
    account and stores a null nobody notices.
    """
    items = _field(subscription, "items")
    data = _field(items, "data", []) if items is not None else []
    for item in data:
        stamp = _field(item, "current_period_end")
        if stamp:
            return datetime.fromtimestamp(int(stamp), timezone.utc).isoformat()
    return None


def _user_for_subscription(db, subscription,
                           fallback_user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Whose subscription this is.

    Customer id first, because that is the durable link and it is written
    before checkout even opens. The metadata copy is the fallback for the one
    case the customer lookup cannot cover: a subscription created outside this
    app, in the Stripe dashboard, against a customer we have never stored.
    """
    customer = _field(subscription, "customer")
    if isinstance(customer, str) and customer:
        found = db.user_by_stripe_customer(customer)
        if found:
            return found

    metadata = _as_dict(_field(subscription, "metadata"))
    # The checkout session's client_reference_id is the last resort: it is
    # written by this app at checkout and names the account directly.
    user_id = metadata.get("user_id") or fallback_user_id
    if user_id:
        found = db.user_by_id(user_id)
        if found:
            # Backfill, so the next event takes the fast path and so the
            # dashboard and the database agree.
            if isinstance(customer, str) and customer:
                db.remember_stripe_customer(found["id"], customer)
            return found
    return None


def apply_subscription(db, subscription,
                       fallback_user_id: Optional[str] = None) -> str:
    """Set somebody's plan from what Stripe says about their subscription.

    The status decides, not the event name. "customer.subscription.updated"
    arrives for a renewal, a cancellation, a failed payment and a plan change
    alike, and reading the status off the object handles all of them with one
    rule instead of four.
    """
    user = _user_for_subscription(db, subscription, fallback_user_id)
    if not user:
        # Loud: this is a payment that did not reach anybody's account.
        return (f"no matching account for customer {_field(subscription, 'customer')} "
                f"/ subscription {_field(subscription, 'id')}")

    # Staff is set by hand in the database and Stripe has no say in it. A
    # cancelled test subscription on a staff account must not quietly put the
    # person testing the site back on two regenerations a week.
    if (user.get("plan") or "").strip().lower() == plans.STAFF:
        return f"{user['id']} is staff; left alone"

    status = str(_field(subscription, "status", "")).lower()
    paid = status in plans.ACTIVE_STATUSES

    db.set_plan(
        user["id"],
        plan=plans.PAID if paid else plans.FREE,
        status=status or None,
        subscription_id=_field(subscription, "id"),
        renews_at=_period_end(subscription),
    )
    return f"{user['id']} -> {plans.PAID if paid else plans.FREE} ({status})"


def handle_event(db, event) -> str:
    """Act on a VERIFIED event. Returns a line for the log.

    Never called with anything `verify` has not already accepted.
    """
    kind = getattr(event, "type", "") or ""
    if kind not in HANDLED_EVENTS:
        return f"ignored {kind}"

    obj = _field(_field(event, "data"), "object")

    if kind == "checkout.session.completed":
        # The session itself carries no subscription status, only a pointer.
        # Fetch the real thing rather than assuming a completed checkout means
        # an active subscription — with some payment methods it does not.
        #
        # _field, not obj.get(): on stripe-python 12+ a Session is not a dict
        # and .get() raises. That was every one of these returning 500.
        subscription_id = _field(obj, "subscription")
        if not subscription_id:
            return "checkout completed with no subscription"
        subscription = _stripe().Subscription.retrieve(subscription_id)
        return apply_subscription(db, subscription,
                                  fallback_user_id=_field(obj, "client_reference_id"))

    return apply_subscription(db, obj)
