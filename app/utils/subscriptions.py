from datetime import datetime, timedelta
from functools import wraps
from types import SimpleNamespace

from flask import abort, current_app, flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models import AgencySubscription, EmailLog, PortalSetting, SMTPSetting, User
from app.utils.authz import is_super_admin_platform, normalized_role
from app.utils.emailer import send_email_smtp


def get_or_create_portal_settings():
    settings = PortalSetting.query.first()
    if settings is None:
        settings = PortalSetting(
            site_name="E-PROJECT",
            site_tagline="Plateforme SaaS multi-agences pour la gestion des etudes a l'étranger",
            site_footer_text="E-PROJECT",
            plan_starter_price=0.0,
            plan_pro_price=0.0,
            plan_enterprise_price=0.0,
            plan_currency="XOF",
            billing_sender_email="eudyproject@gmail.com",
            expiry_notice_days=7,
        )
        db.session.add(settings)
        db.session.commit()
    return settings


def get_plan_catalog(settings):
    currency = (settings.plan_currency or "XOF").upper()
    return [
        {
            "code": "starter",
            "name": "Starter",
            "price": float(settings.plan_starter_price or 0.0),
            "currency": currency,
            "features": [
                "CRM étudiants + documents",
                "Dashboard branche",
                "Emails basiques",
            ],
        },
        {
            "code": "pro",
            "name": "Pro",
            "price": float(settings.plan_pro_price or 0.0),
            "currency": currency,
            "features": [
                "RDV avances + tokens",
                "Emails personnalises + logos",
                "Suivi procedures et commissions",
            ],
        },
        {
            "code": "enterprise",
            "name": "Enterprise",
            "price": float(settings.plan_enterprise_price or 0.0),
            "currency": currency,
            "features": [
                "Multi-pays complet",
                "Rapports globaux",
                "Support prioritaire",
            ],
        },
    ]


def price_for_plan(settings, plan_code):
    plans = {p["code"]: p for p in get_plan_catalog(settings)}
    row = plans.get(plan_code or "")
    if not row:
        return 0.0, (settings.plan_currency or "XOF").upper()
    return float(row["price"] or 0.0), row["currency"]


def subscriptions_enforced(settings=None):
    settings = settings or get_or_create_portal_settings()
    prices = [
        float(settings.plan_starter_price or 0.0),
        float(settings.plan_pro_price or 0.0),
        float(settings.plan_enterprise_price or 0.0),
    ]
    return any(price > 0 for price in prices)


def _resolve_smtp_for_subscription_notice(subscription):
    branch_smtp = SMTPSetting.query.filter_by(branch_id=subscription.branch_id).first()
    if branch_smtp:
        return branch_smtp
    global_smtp = SMTPSetting.query.filter_by(branch_id=None).first()
    if global_smtp:
        return global_smtp
    return None


def _send_subscription_notice(subscription, subject, html_body, text_body):
    smtp = _resolve_smtp_for_subscription_notice(subscription)
    if not smtp:
        return False, "SMTP indisponible"
    owner = User.query.get(subscription.owner_user_id)
    if not owner or not owner.email:
        return False, "Owner email manquant"
    try:
        send_email_smtp(smtp, owner.email, subject, html_body, text_body)
        db.session.add(
            EmailLog(
                branch_id=subscription.branch_id,
                to_email=owner.email,
                subject=subject,
                status="sent",
                sent_by=None,
            )
        )
        db.session.commit()
        return True, None
    except Exception as exc:
        db.session.add(
            EmailLog(
                branch_id=subscription.branch_id,
                to_email=owner.email,
                subject=subject,
                status="failed",
                error=str(exc),
                sent_by=None,
            )
        )
        db.session.commit()
        return False, str(exc)



def _resolve_platform_smtp():
    global_smtp = SMTPSetting.query.filter_by(branch_id=None).first()
    if global_smtp:
        return global_smtp

    cfg = current_app.config
    required = [cfg.get("SMTP_HOST"), cfg.get("SMTP_USERNAME"), cfg.get("SMTP_PASSWORD"), cfg.get("SMTP_FROM")]
    if all(required):
        return SimpleNamespace(
            host=cfg.get("SMTP_HOST"),
            port=cfg.get("SMTP_PORT", 587),
            username=cfg.get("SMTP_USERNAME"),
            password=cfg.get("SMTP_PASSWORD"),
            from_email=cfg.get("SMTP_FROM"),
            use_tls=cfg.get("SMTP_TLS", True),
        )
    return None


def send_subscription_transactional_email(subscription, subject, html_body, text_body, sent_by=None):
    if subscription is None:
        return False, "Subscription indisponible"

    owner = User.query.get(subscription.owner_user_id)
    if not owner or not owner.email:
        return False, "Owner email manquant"

    smtp = _resolve_platform_smtp()
    if not smtp:
        return False, "SMTP plateforme indisponible"

    try:
        send_email_smtp(smtp, owner.email, subject, html_body, text_body)
        db.session.add(
            EmailLog(
                branch_id=subscription.branch_id,
                to_email=owner.email,
                subject=subject,
                status="sent",
                sent_by=sent_by,
            )
        )
        db.session.commit()
        return True, None
    except Exception as exc:
        db.session.add(
            EmailLog(
                branch_id=subscription.branch_id,
                to_email=owner.email,
                subject=subject,
                status="failed",
                error=str(exc),
                sent_by=sent_by,
            )
        )
        db.session.commit()
        return False, str(exc)

def process_subscription_notifications():
    settings = get_or_create_portal_settings()
    notice_days = int(settings.expiry_notice_days or 7)
    now = datetime.utcnow()
    subscriptions = AgencySubscription.query.filter(AgencySubscription.status.in_(["active", "expired"])).all()
    dirty = False

    for sub in subscriptions:
        if not sub.ends_at:
            continue
        days_left = (sub.ends_at.date() - now.date()).days

        if sub.status == "active" and days_left <= notice_days:
            already_sent_recently = sub.last_warning_sent_at and (now - sub.last_warning_sent_at) < timedelta(hours=23)
            if not already_sent_recently:
                manage_url = url_for("auth.subscription_status", _external=True)
                subject = "Votre abonnement va expirer"
                html_body = (
                    "<p>Bonjour,</p>"
                    f"<p>Votre abonnement arrive à expiration le <strong>{sub.ends_at.strftime('%d/%m/%Y')}</strong>.</p>"
                    f"<p>Renouvelez votre plan ici : <a href='{manage_url}'>Gérer mon abonnement</a></p>"
                )
                text_body = (
                    "Bonjour,\n\n"
                    f"Votre abonnement expire le {sub.ends_at.strftime('%d/%m/%Y')}.\n"
                    f"Gérer l'abonnement : {manage_url}"
                )
                _send_subscription_notice(sub, subject, html_body, text_body)
                sub.last_warning_sent_at = now
                dirty = True

        if sub.ends_at < now:
            if sub.status != "expired":
                sub.status = "expired"
                dirty = True
            already_sent_recently = sub.last_expired_sent_at and (now - sub.last_expired_sent_at) < timedelta(hours=23)
            if not already_sent_recently:
                manage_url = url_for("auth.subscription_status", _external=True)
                subject = "Compte expiré - réabonnement requis"
                html_body = (
                    "<p>Bonjour,</p>"
                    "<p>Votre abonnement est expiré.</p>"
                    f"<p>Pour réactiver votre compte : <a href='{manage_url}'>Renouveler maintenant</a></p>"
                )
                text_body = (
                    "Bonjour,\n\n"
                    "Votre abonnement est expiré.\n"
                    f"Renouveler: {manage_url}"
                )
                _send_subscription_notice(sub, subject, html_body, text_body)
                sub.last_expired_sent_at = now
                dirty = True

    if dirty:
        db.session.commit()


def current_user_subscription():
    if not current_user.is_authenticated:
        return None
    return get_subscription_for_user(current_user)


def is_billable_subscription(sub):
    if sub is None:
        return False
    owner = getattr(sub, "owner_user", None)
    if owner is None and getattr(sub, "owner_user_id", None):
        owner = User.query.get(sub.owner_user_id)
    if owner is None:
        return True
    owner_main_branch_id = getattr(owner, "branch_id", None)
    if owner_main_branch_id is None:
        return True
    return int(sub.branch_id or 0) == int(owner_main_branch_id)


def owner_billable_subscription(owner_user_id):
    if not owner_user_id:
        return None
    return (
        AgencySubscription.query.join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.owner_user_id == owner_user_id)
        .filter(AgencySubscription.branch_id == User.branch_id)
        .order_by(AgencySubscription.id.asc())
        .first()
    )


def get_subscription_for_user(user):
    if not user:
        return None
    role = normalized_role(getattr(user, "role", None))
    if role == "FOUNDER":
        by_owner = owner_billable_subscription(user.id)
        if by_owner:
            return by_owner
        fallback = AgencySubscription.query.filter_by(owner_user_id=user.id).order_by(AgencySubscription.id.asc()).first()
        if fallback:
            return fallback

    branch_id = getattr(user, "branch_id", None)
    if not branch_id:
        return None

    branch_sub = AgencySubscription.query.filter_by(branch_id=branch_id).first()
    if branch_sub and not is_billable_subscription(branch_sub):
        owner_sub = owner_billable_subscription(branch_sub.owner_user_id)
        if owner_sub:
            return owner_sub
    return branch_sub


def user_has_active_subscription(user):
    sub = get_subscription_for_user(user)
    # Un abonnement explicitement expire doit toujours bloquer, meme en mode gratuit.
    if sub and sub.status == "expired":
        return False
    if not subscriptions_enforced():
        return True
    if not sub:
        return False
    if sub.status != "active":
        return False
    if sub.ends_at and sub.ends_at < datetime.utcnow():
        return False
    return True


def is_subscription_active_for_user(user):
    return user_has_active_subscription(user)



PLAN_RANKS = {
    "starter": 1,
    "pro": 2,
    "enterprise": 3,
}


def _normalize_plan_code(plan_code):
    value = (plan_code or "starter").strip().lower()
    return value if value in PLAN_RANKS else "starter"


def current_user_plan_code(user=None):
    user = user or current_user
    if not user:
        return "starter"
    if is_super_admin_platform(user):
        return "enterprise"

    sub = get_subscription_for_user(user)
    if not sub:
        return "starter"
    return _normalize_plan_code(getattr(sub, "plan_code", None))


def user_plan_allows(min_plan="starter", user=None):
    user = user or current_user
    if not user:
        return False
    if is_super_admin_platform(user):
        return True

    required = PLAN_RANKS.get(_normalize_plan_code(min_plan), 1)
    current = PLAN_RANKS.get(current_user_plan_code(user), 1)
    return current >= required


def plan_required(min_plan="starter", feature_label=""):
    required_plan = _normalize_plan_code(min_plan)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if user_plan_allows(required_plan, current_user):
                return view_func(*args, **kwargs)

            plan_name = required_plan.upper()
            suffix = f" ({feature_label})" if feature_label else ""
            flash(f"Fonction reservee au plan {plan_name}{suffix}.", "warning")
            return redirect(url_for("dashboard.index"))

        return wrapped

    return decorator


