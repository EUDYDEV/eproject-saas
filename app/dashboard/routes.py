from datetime import datetime, timedelta
import re

from sqlalchemy import and_, case, func, or_
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.appointments.forms import EventSettingsForm
from app.auth.forms import AgencySignupForm
from app.dashboard.forms import ArrivalSupportForm, CommissionPayForm, CommissionRuleForm
from app.extensions import db
from app.models import (
    AgencySubscription,
    ArrivalSupport,
    AuditLog,
    Branch,
    CasePayment,
    CommissionRecord,
    CommissionRule,
    Event,
    EventSlot,
    Entity,
    Membership,
    PortalSetting,
    School,
    StudyCase,
    Student,
    StudentDocument,
    User,
)
from app.utils.audit import add_audit_log
from app.utils.authz import can_access_branch, is_super_admin_platform, normalized_role, role_required, scope_query_by_branch, user_branch_ids
from app.utils.commissions import sync_commissions_for_cases
from app.utils.subscriptions import get_or_create_portal_settings, get_plan_catalog, plan_required


dashboard_bp = Blueprint("dashboard", __name__)


def _get_or_create_dashboard_settings(branch_id=None):
    settings = PortalSetting.query.filter_by(branch_id=branch_id).first()
    if settings is None:
        settings = PortalSetting(branch_id=branch_id, max_appointments_per_day=10)
        db.session.add(settings)
        db.session.commit()
    return settings



def _parse_orientation_date(raw_value):
    value = (raw_value or "").strip()
    if not value:
        return None
    formats = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_slot_line(line):
    text = (line or "").strip()
    if not text:
        return None
    text = text.replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in re.split(r"\s*-\s*", text) if p.strip()]
    if len(parts) != 2:
        return None
    try:
        start_time = datetime.strptime(parts[0], "%H:%M").time()
        end_time = datetime.strptime(parts[1], "%H:%M").time()
    except ValueError:
        return None
    if end_time <= start_time:
        return None
    return (start_time, end_time)


def _sync_dashboard_event_settings(branch_id, settings):
    if not branch_id or settings is None:
        return {"ok": False, "error": "missing_branch"}

    date_value = _parse_orientation_date(settings.orientation_date)
    if date_value is None:
        return {"ok": False, "error": "invalid_date"}

    slot_lines = (settings.appointment_slots or "").splitlines()
    parsed_slots = []
    for line in slot_lines:
        parsed = _parse_slot_line(line)
        if parsed:
            parsed_slots.append(parsed)
    if not parsed_slots:
        return {"ok": False, "error": "invalid_slots"}

    event_slug = f"dashboard-rdv-{branch_id}"
    event = Event.query.filter_by(slug=event_slug).first()
    if event is None:
        event = Event(branch_id=branch_id, slug=event_slug)
        db.session.add(event)

    event.title = (settings.event_title or "RDV").strip() or "RDV"
    event.location = (settings.orientation_address or "").strip() or None
    event.start_date = date_value
    event.end_date = date_value
    event.day_start_time = min(s[0] for s in parsed_slots)
    event.day_end_time = max(s[1] for s in parsed_slots)
    event.slot_minutes = max(
        5,
        int((datetime.combine(date_value, parsed_slots[0][1]) - datetime.combine(date_value, parsed_slots[0][0])).total_seconds() // 60),
    )
    event.max_per_day = max(1, int(settings.max_appointments_per_day or 10))
    event.is_active = True

    db.session.flush()

    desired_keys = set()
    for start_time, end_time in parsed_slots:
        start_dt = datetime.combine(date_value, start_time)
        end_dt = datetime.combine(date_value, end_time)
        desired_keys.add((start_dt, end_dt))
        exists = EventSlot.query.filter_by(event_id=event.id, start_datetime=start_dt, end_datetime=end_dt).first()
        if exists is None:
            db.session.add(
                EventSlot(
                    event_id=event.id,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    capacity=1,
                    booked_count=0,
                )
            )

    existing_slots = EventSlot.query.filter_by(event_id=event.id).all()
    for slot in existing_slots:
        key = (slot.start_datetime, slot.end_datetime)
        same_day = slot.start_datetime and slot.start_datetime.date() == date_value
        if same_day and key not in desired_keys and (slot.booked_count or 0) == 0:
            db.session.delete(slot)

    return {"ok": True, "event_id": event.id}
def _current_chat_branch_id():
    if not current_user.is_authenticated:
        return None
    if is_super_admin_platform(current_user):
        scoped = session.get("it_scope_branch_id")
        if scoped:
            return int(scoped)
        return None
    if getattr(current_user, "branch_id", None):
        return int(current_user.branch_id)
    membership = Membership.query.filter_by(user_id=current_user.id).order_by(Membership.id.asc()).first()
    if membership and membership.branch_id:
        return int(membership.branch_id)
    return None


def _chat_target_branch_id():
    current_branch_id = _current_chat_branch_id()
    if not is_super_admin_platform(current_user):
        return current_branch_id

    def _safe_int(value):
        if value is None:
            return None
        try:
            as_text = str(value).strip()
            if not as_text:
                return None
            return int(as_text)
        except (TypeError, ValueError):
            return None

    raw = _safe_int(request.args.get("branch_id"))
    if raw is None:
        raw = _safe_int(request.form.get("branch_id"))
    if raw and can_access_branch(raw):
        return int(raw)
    return current_branch_id

def _is_support_user(user):
    if user is None:
        return False
    if is_super_admin_platform(user):
        return True
    return normalized_role(getattr(user, "role", None)) in ("IT", "INFORMATICIEN")


def _support_sender_user_id():
    support_user = (
        User.query.filter(User.is_active.is_(True))
        .filter(or_(User.platform_role == "SUPER_ADMIN_PLATFORM", User.role.in_(["IT", "INFORMATICIEN"])))
        .order_by(User.id.asc())
        .first()
    )
    if support_user:
        return support_user.id
    return current_user.id


def _support_identity_name(seed=0):
    names = ["Eudy", "Francois", "Grace", "Marlene", "Eli Charles"]
    try:
        idx = int(seed if seed is not None else 0)
    except (TypeError, ValueError):
        idx = 0
    return names[idx % len(names)]


def _detect_message_intent(message):
    low = (message or "").strip().lower()
    if not low:
        return "empty"

    greetings = ["salut", "bonjour", "bonsoir", "hello", "slt", "bjr", "cc", "coucou"]
    has_greeting = any(low == g or low.startswith(g + " ") for g in greetings)
    has_help = any(k in low for k in ["aide", "aider", "besoin", "assistance", "help"])

    # Priorite: technique > aide > business > salutation.
    if _is_technical_message(low):
        return "technical"
    if has_help:
        return "help"
    if any(k in low for k in ["paiement", "payer", "abonnement", "réabonnément", "facture", "reference"]):
        return "billing"
    if any(k in low for k in ["rdv", "rendez", "rendez-vous", "appointment"]):
        return "appointment"
    if "merci" in low:
        return "thanks"
    if has_greeting:
        return "greeting"
    return "general"


def _is_technical_message(message):
    low = (message or "").lower()
    technical_keywords = [
        "bug", "erreur", "problème", "technique", "serveur", "connexion", "login", "mot de passe",
        "smtp", "mail", "500", "404", "csrf", "api", "import", "export", "upload", "paiement",
        "abonnement", "dashboard", "freeze", "bloque",
    ]
    return any(k in low for k in technical_keywords)


def _build_ai_reply(user_message, client_name, support_name, technical, include_intro=True):
    intro = f"Bonjour {client_name}, je suis {support_name}, service clients E-PROJECT. " if include_intro else ""
    intent = _detect_message_intent(user_message)

    if technical or intent == "technical":
        return (
            f"{intro}Veuillez nous dire votre problème et un de nos agents va s'en charger. "
            "Un de nos agents va vous reprendre dans quelques instants."
        )
    if intent == "greeting":
        return (
            f"{intro}Bienvenue chez E-PROJECT. "
            "Veuillez nous dire votre problÃƒÂ¨me et un de nos agents va s'en charger."
        )
    if intent == "help":
        return (
            f"{intro}Veuillez nous dire votre problÃƒÂ¨me et un de nos agents va s'en charger. "
            "Pour aller vite, indiquez si cela concerne: compte, étudiant, paiement, RDV ou dossier."
        )
    if intent == "billing":
        return (
            f"{intro}Je peux vous aider sur les paiements et l'abonnement. "
            "Envoyez la reference ou le problème rencontre, et je traite votre demande rapidement."
        )
    if intent == "appointment":
        return (
            f"{intro}Je peux vous accompagner pour les rendez-vous. "
            "Precisez la date souhaitee ou la modification a faire."
        )
    if intent == "thanks":
        return f"{intro}Avec plaisir. Je reste disponible si vous avez une autre demande."
    if intent == "empty":
        return f"{intro}Je vous ecoute. Ecrivez votre demande et je vous reponds tout de suite."
    return (
        f"{intro}J'ai bien recu votre demande. "
        "Je l'analyse et je vous apporte une reponse claire dans quelques instants."
    )


def _build_it_saas_context():
    billable_q = AgencySubscription.query.join(User, AgencySubscription.owner_user_id == User.id).filter(
        AgencySubscription.branch_id == User.branch_id
    )
    total_subs = billable_q.count()
    expired_subs = billable_q.filter(AgencySubscription.status == "expired").count()
    pending_review_subs = billable_q.filter(AgencySubscription.status == "pending_review").count()
    pending_subs = billable_q.filter(AgencySubscription.status == "pending").count()
    it_subscription_alerts = {
        "total": total_subs,
        "expired": expired_subs,
        "pending_review": pending_review_subs,
        "pending": pending_subs,
    }
    it_pending_review_rows = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
        .order_by(AgencySubscription.updated_at.desc(), AgencySubscription.created_at.desc())
        .filter(AgencySubscription.status == "pending_review")
        .limit(20)
        .all()
    )

    raw_sub_year = request.args.get("sub_year", type=int)
    raw_sub_month = request.args.get("sub_month", type=int)
    paid_rows_all = billable_q.filter(AgencySubscription.paid_at.isnot(None)).all()
    year_options = sorted({row.paid_at.year for row in paid_rows_all if row.paid_at} | {datetime.utcnow().year}, reverse=True)
    selected_sub_year = raw_sub_year if raw_sub_year in year_options else datetime.utcnow().year
    selected_sub_month = raw_sub_month if raw_sub_month in range(0, 13) else 0

    paid_rows_filtered = [r for r in paid_rows_all if r.paid_at and r.paid_at.year == selected_sub_year]
    if selected_sub_month:
        paid_rows_filtered = [r for r in paid_rows_filtered if r.paid_at.month == selected_sub_month]

    collected_amount = float(sum(float(r.amount or 0) for r in paid_rows_filtered))
    pending_review_amount = float(
        db.session.query(func.coalesce(func.sum(AgencySubscription.amount), 0.0))
        .select_from(AgencySubscription)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id, AgencySubscription.status == "pending_review")
        .scalar()
        or 0.0
    )
    pending_amount = float(
        db.session.query(func.coalesce(func.sum(AgencySubscription.amount), 0.0))
        .select_from(AgencySubscription)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id, AgencySubscription.status == "pending")
        .scalar()
        or 0.0
    )

    monthly_income = [0.0] * 12
    for row in paid_rows_all:
        if not row.paid_at or row.paid_at.year != selected_sub_year:
            continue
        monthly_income[row.paid_at.month - 1] += float(row.amount or 0)

    it_billing_filters = {
        "year_options": year_options,
        "selected_year": selected_sub_year,
        "selected_month": selected_sub_month,
    }
    it_billing_summary = {
        "collected_amount": round(collected_amount, 2),
        "pending_review_amount": round(pending_review_amount, 2),
        "pending_amount": round(pending_amount, 2),
        "pending_review_count": pending_review_subs,
    }
    it_billing_chart_data = {
        "split_labels": ["Encaisse", "En validation", "En attente paiement"],
        "split_values": [round(collected_amount, 2), round(pending_review_amount, 2), round(pending_amount, 2)],
        "month_labels": ["Jan", "Fev", "Mar", "Avr", "Mai", "Jun", "Jul", "Aou", "Sep", "Oct", "Nov", "Dec"],
        "month_values": [round(v, 2) for v in monthly_income],
    }

    client_q = (request.args.get("client_q") or "").strip()
    client_status = (request.args.get("client_status") or "all").strip().lower()
    allowed_statuses = {"all", "active", "expired", "pending_review", "pending"}
    if client_status not in allowed_statuses:
        client_status = "all"

    client_rows_query = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
    )
    if client_status != "all":
        client_rows_query = client_rows_query.filter(AgencySubscription.status == client_status)
    if client_q:
        like_q = f"%{client_q}%"
        client_rows_query = client_rows_query.filter(
            or_(
                Branch.name.ilike(like_q),
                User.username.ilike(like_q),
                User.email.ilike(like_q),
                AgencySubscription.plan_code.ilike(like_q),
                AgencySubscription.payment_reference.ilike(like_q),
            )
        )
    it_client_rows = client_rows_query.order_by(Branch.name.asc(), User.username.asc()).limit(30).all()

    return {
        "it_subscription_alerts": it_subscription_alerts,
        "it_pending_review_rows": it_pending_review_rows,
        "it_billing_filters": it_billing_filters,
        "it_billing_summary": it_billing_summary,
        "it_billing_chart_data": it_billing_chart_data,
        "it_client_rows": it_client_rows,
        "it_client_filters": {"q": client_q, "status": client_status},
    }
@dashboard_bp.route("/it")
@login_required
@role_required("IT")
def it_saas_dashboard():
    if not is_super_admin_platform(current_user):
        return redirect(url_for("dashboard.index"))
    session["it_ui_mode"] = "saas"
    return render_template("dashboard/it_saas.html", **_build_it_saas_context())


@dashboard_bp.route("/agency")
@login_required
@role_required("IT")
def it_agency_dashboard():
    if not is_super_admin_platform(current_user):
        return redirect(url_for("dashboard.index"))
    # Regle demandee: on redemande toujours de choisir/rechercher une agence.
    session["it_ui_mode"] = "saas"
    flash("Selectionne une agence pour ouvrir la vue agence IT.", "warning")
    return redirect(url_for("dashboard.it_saas_dashboard", open_agency_selector=1))



@dashboard_bp.route("/app/<string:agency_slug>/dashboard")
@login_required
def agency_slug_dashboard(agency_slug):
    branch = Branch.query.filter_by(slug=(agency_slug or "").strip().lower()).first_or_404()
    if not can_access_branch(branch.id):
        return redirect(url_for("dashboard.index"))
    if is_super_admin_platform(current_user):
        session["it_scope_branch_id"] = branch.id
        session["it_ui_mode"] = "agency"
        return redirect(url_for("dashboard.index", agency_view=1, branch_id=branch.id))
    return redirect(url_for("dashboard.index", branch_id=branch.id))


@dashboard_bp.route("/api/<string:agency_slug>/students")
@login_required
def api_students_by_agency(agency_slug):
    branch = Branch.query.filter_by(slug=(agency_slug or "").strip().lower()).first_or_404()
    if not can_access_branch(branch.id):
        return {"error": "forbidden"}, 403
    rows = (
        scope_query_by_branch(Student.query, Student)
        .filter(Student.branch_id == branch.id)
        .order_by(Student.nom.asc(), Student.prenoms.asc(), Student.matricule.asc())
        .limit(100)
        .all()
    )
    return {
        "agency": {"id": branch.id, "name": branch.name, "slug": branch.slug},
        "items": [
            {
                "id": s.id,
                "matricule": s.matricule,
                "nom": s.nom,
                "prenoms": s.prenoms,
                "filiere": s.filiere,
                "niveau": s.niveau,
            }
            for s in rows
        ],
    }


@dashboard_bp.route("/chat")
@login_required
def chat_room():
    selected_branch_id = _chat_target_branch_id()
    branches = []
    if is_super_admin_platform(current_user):
        branches = (
            Branch.query.join(AgencySubscription, AgencySubscription.branch_id == Branch.id)
            .order_by(Branch.name.asc())
            .all()
        )
    elif selected_branch_id:
        branch = Branch.query.get(selected_branch_id)
        if branch:
            branches = [branch]

    selected_branch = Branch.query.get(selected_branch_id) if selected_branch_id else None
    return render_template(
        "dashboard/chat.html",
        branches=branches,
        selected_branch=selected_branch,
        selected_branch_id=selected_branch_id or 0,
        is_it_saas_mode=bool(is_super_admin_platform(current_user) and session.get("it_ui_mode") == "saas"),
        can_close_chat=_is_support_user(current_user),
    )


@dashboard_bp.route("/chat/messages")
@login_required
def chat_messages():
    branch_id = _chat_target_branch_id()
    if not branch_id:
        return jsonify({"items": [], "requires_agency": True, "branch_id": None})
    if not can_access_branch(branch_id):
        return jsonify({"error": "forbidden"}), 403

    # Mark branch chat as seen by support so global notification counter can decrease.
    if _is_support_user(current_user):
        latest_client = (
            AuditLog.query.filter(
                AuditLog.branch_id == branch_id,
                AuditLog.type_event == "chat_message",
                AuditLog.action == "client_message",
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        if latest_client:
            latest_seen = (
                AuditLog.query.filter(
                    AuditLog.branch_id == branch_id,
                    AuditLog.type_event == "chat_thread",
                    AuditLog.action == "it_seen",
                )
                .order_by(AuditLog.id.desc())
                .first()
            )
            latest_seen_at = latest_seen.created_at if latest_seen and latest_seen.created_at else None
            latest_client_at = latest_client.created_at if latest_client.created_at else datetime.utcnow()
            if latest_seen_at is None or latest_seen_at < latest_client_at:
                db.session.add(
                    AuditLog(
                        branch_id=branch_id,
                        user_id=current_user.id,
                        type_event="chat_thread",
                        action="it_seen",
                        details="Messages service clients consultes.",
                    )
                )
                db.session.commit()

    after_id = max(request.args.get("after_id", type=int) or 0, 0)
    limit = min(max(request.args.get("limit", type=int) or 40, 1), 100)

    query = AuditLog.query.filter(
        AuditLog.branch_id == branch_id,
        AuditLog.type_event == "chat_message",
    )
    last_close = (
        AuditLog.query.filter(
            AuditLog.branch_id == branch_id,
            AuditLog.type_event == "chat_thread",
            AuditLog.action == "closed",
        )
        .order_by(AuditLog.id.desc())
        .first()
    )
    if last_close:
        query = query.filter(AuditLog.id > last_close.id)
    if after_id:
        rows = query.filter(AuditLog.id > after_id).order_by(AuditLog.id.asc()).limit(limit).all()
    else:
        rows = query.order_by(AuditLog.id.desc()).limit(limit).all()
        rows.reverse()

    items = []
    for row in rows:
        user = row.user
        action = (row.action or "").strip().lower()
        if action in ("service_reply", "ai_reply", "ai_escalation_reply"):
            sender_name = "Service clients"
            sender_role = "E-PROJECT"
        else:
            sender_name = "Utilisateur"
            sender_role = ""
            if user:
                sender_name = user.display_name or user.username or "Utilisateur"
                sender_role = normalized_role(user.role or "")
        items.append(
            {
                "id": row.id,
                "message": row.details or "",
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "created_at_label": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
                "sender": sender_name,
                "sender_role": sender_role,
                "mine": bool(row.user_id == current_user.id and action not in ("ai_reply", "ai_escalation_reply")),
            }
        )

    return jsonify({"items": items, "requires_agency": False, "branch_id": branch_id})


@dashboard_bp.route("/chat/close", methods=["POST"])
@login_required
def chat_close():
    if not _is_support_user(current_user):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    branch_id = _chat_target_branch_id()
    if not branch_id:
        return jsonify({"ok": False, "error": "agency_required"}), 400
    if not can_access_branch(branch_id):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    closed = AuditLog(
        branch_id=branch_id,
        user_id=current_user.id,
        type_event="chat_thread",
        action="closed",
        details="Conversation cloturee par le service clients.",
    )
    db.session.add(closed)
    db.session.add(
        AuditLog(
            branch_id=branch_id,
            user_id=current_user.id,
            type_event="chat_alert",
            action="technical_handled",
            details="Conversation cloturee.",
        )
    )
    db.session.commit()
    return jsonify({"ok": True, "closed_id": closed.id, "closed_at_label": closed.created_at.strftime("%Y-%m-%d %H:%M") if closed.created_at else ""})


@dashboard_bp.route("/chat/send", methods=["POST"])
@login_required
def chat_send():
    branch_id = _chat_target_branch_id()
    if not branch_id:
        return jsonify({"ok": False, "error": "agency_required"}), 400
    if not can_access_branch(branch_id):
        return jsonify({"ok": False, "error": "forbidden"}), 403

    message = (request.form.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "empty_message"}), 400
    if len(message) > 1500:
        message = message[:1500]

    sender_is_support = _is_support_user(current_user)
    sender_action = "service_reply" if sender_is_support else "client_message"

    log_row = AuditLog(
        branch_id=branch_id,
        user_id=current_user.id,
        type_event="chat_message",
        action=sender_action,
        details=message,
    )
    db.session.add(log_row)

    auto_reply_item = None

    if sender_is_support:
        had_pending = (
            AuditLog.query.filter(
                AuditLog.branch_id == branch_id,
                AuditLog.type_event == "chat_alert",
                AuditLog.action == "technical_pending",
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        if had_pending:
            db.session.add(
                AuditLog(
                    branch_id=branch_id,
                    user_id=current_user.id,
                    type_event="chat_alert",
                    action="technical_handled",
                    details="Demande technique prise en charge par le service clients.",
                )
            )
    else:
        client_name = (current_user.display_name or current_user.username or "client").strip()
        support_name = _support_identity_name(current_user.id)
        is_technical = _is_technical_message(message)
        previous_ai = (
            AuditLog.query.filter(
                AuditLog.branch_id == branch_id,
                AuditLog.type_event == "chat_message",
                AuditLog.action.in_(["ai_reply", "ai_escalation_reply", "service_reply"]),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        include_intro = previous_ai is None
        ai_message = _build_ai_reply(message, client_name, support_name, is_technical, include_intro=include_intro)
        support_user_id = _support_sender_user_id()
        ai_action = "ai_escalation_reply" if is_technical else "ai_reply"
        ai_log = AuditLog(
            branch_id=branch_id,
            user_id=support_user_id,
            type_event="chat_message",
            action=ai_action,
            details=ai_message,
        )
        db.session.add(ai_log)
        if is_technical:
            db.session.add(
                AuditLog(
                    branch_id=branch_id,
                    user_id=support_user_id,
                    type_event="chat_alert",
                    action="technical_pending",
                    details=f"Demande technique de {client_name}",
                )
            )
        auto_reply_item = {
            "id": None,
            "message": ai_message,
            "created_at": None,
            "created_at_label": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            "sender": "Service clients",
            "sender_role": "E-PROJECT",
            "mine": False,
        }

    db.session.commit()

    if sender_is_support:
        sender_name = "Service clients"
        sender_role = "E-PROJECT"
    else:
        sender_name = current_user.display_name or current_user.username or "Utilisateur"
        sender_role = normalized_role(current_user.role or "")

    item = {
        "id": log_row.id,
        "message": log_row.details or "",
        "created_at": log_row.created_at.isoformat() if log_row.created_at else None,
        "created_at_label": log_row.created_at.strftime("%Y-%m-%d %H:%M") if log_row.created_at else "",
        "sender": sender_name,
        "sender_role": sender_role,
        "mine": True,
    }

    if auto_reply_item is not None:
        # Refresh ai reply with persisted row metadata.
        latest_ai = (
            AuditLog.query.filter(
                AuditLog.branch_id == branch_id,
                AuditLog.type_event == "chat_message",
                AuditLog.action.in_(["ai_reply", "ai_escalation_reply"]),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        if latest_ai:
            auto_reply_item["id"] = latest_ai.id
            auto_reply_item["created_at"] = latest_ai.created_at.isoformat() if latest_ai.created_at else None
            auto_reply_item["created_at_label"] = latest_ai.created_at.strftime("%Y-%m-%d %H:%M") if latest_ai.created_at else auto_reply_item["created_at_label"]

    return jsonify({"ok": True, "item": item, "auto_reply": auto_reply_item})


@dashboard_bp.route("/a-propos")
def public_about():
    settings = get_or_create_portal_settings()
    return render_template("public/about.html", settings=settings)


@dashboard_bp.route("/services")
def public_services():
    settings = get_or_create_portal_settings()
    return render_template("public/services.html", settings=settings)


@dashboard_bp.route("/contact")
def public_contact():
    settings = get_or_create_portal_settings()
    return render_template("public/contact.html", settings=settings)

@dashboard_bp.route("/", methods=["GET", "POST"])
def index():
    if not current_user.is_authenticated:
        settings = get_or_create_portal_settings()
        plans = get_plan_catalog(settings)
        plan_payment_links = {
            "starter": settings.payment_link_starter or settings.payment_link,
            "pro": settings.payment_link_pro or settings.payment_link,
            "enterprise": settings.payment_link_enterprise or settings.payment_link,
        }
        return render_template(
            "home.html",
            signup_form=AgencySignupForm(),
            plans=plans,
            payment_link=settings.payment_link,
            plan_payment_links=plan_payment_links,
        )

    role = normalized_role(current_user.role)
    if is_super_admin_platform(current_user) and request.args.get("agency_view") != "1":
        return redirect(url_for("dashboard.it_saas_dashboard"))
    it_agency_mode = is_super_admin_platform(current_user) and request.args.get("agency_view") == "1"
    if it_agency_mode:
        session["it_ui_mode"] = "agency"

    settings_branch_id = None
    if is_super_admin_platform(current_user) and it_agency_mode:
        settings_branch_id = session.get("it_scope_branch_id")
    elif normalized_role(current_user.role) in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE"):
        settings_branch_id = current_user.branch_id

    settings = _get_or_create_dashboard_settings(settings_branch_id)
    settings_form = EventSettingsForm(obj=settings)
    if settings_form.validate_on_submit():
        if normalized_role(current_user.role) not in ("FOUNDER", "ADMIN_BRANCH", "IT"):
            flash("Acces refuse.", "danger")
            return redirect(url_for("dashboard.index"))

        settings.event_title = (settings_form.event_title.data or "").strip() or None
        settings.orientation_date = (settings_form.orientation_date.data or "").strip() or None
        settings.orientation_address = (settings_form.orientation_address.data or "").strip() or None
        settings.orientation_phone = (settings_form.orientation_phone.data or "").strip() or None
        settings.representative_name = (settings_form.representative_name.data or "").strip() or None
        settings.appointment_slots = (settings_form.appointment_slots.data or "").strip() or None
        settings.max_appointments_per_day = settings_form.max_appointments_per_day.data
        sync_result = _sync_dashboard_event_settings(settings.branch_id, settings)
        db.session.commit()
        add_audit_log(current_user.id, "portal_settings_update", "Mise à jour infos événement")
        if sync_result.get("ok"):
            flash("Infos événement mises a jour et synchronisees avec les RDV.", "success")
        else:
            flash("Infos événement mises a jour. Corrigez date et creneaux pour activer les RDV.", "warning")
        return redirect(url_for("dashboard.index"))

    it_scope_branch_id = session.get("it_scope_branch_id") if is_super_admin_platform(current_user) else None

    if is_super_admin_platform(current_user):
        if it_agency_mode:
            if not it_scope_branch_id:
                flash("Selectionne une agence pour ouvrir la vue agence IT.", "warning")
                return redirect(url_for("dashboard.it_saas_dashboard"))
            branch_filter_options = Branch.query.filter(Branch.id == it_scope_branch_id).order_by(Branch.name.asc()).all()
        elif it_scope_branch_id:
            branch_filter_options = Branch.query.filter(Branch.id == it_scope_branch_id).order_by(Branch.name.asc()).all()
        else:
            branch_filter_options = Branch.query.order_by(Branch.name.asc()).all()
    else:
        allowed_ids = user_branch_ids(current_user)
        if allowed_ids:
            branch_filter_options = Branch.query.filter(Branch.id.in_(allowed_ids)).order_by(Branch.name.asc()).all()
        else:
            branch_filter_options = []

    if is_super_admin_platform(current_user) and it_agency_mode and it_scope_branch_id:
        selected_branch_id = it_scope_branch_id
    else:
        selected_branch_id = request.args.get("branch_id", type=int) or (it_scope_branch_id or 0)
    allowed_branch_ids = {b.id for b in branch_filter_options}
    if selected_branch_id and selected_branch_id not in allowed_branch_ids:
        selected_branch_id = 0

    scoped_students = scope_query_by_branch(Student.query, Student)
    if selected_branch_id:
        scoped_students = scoped_students.filter(Student.branch_id == selected_branch_id)

    total = scoped_students.count()
    actifs = scoped_students.filter_by(statut="actif").count()
    suspendus = scoped_students.filter_by(statut="suspendu").count()
    anciens = scoped_students.filter_by(statut="ancien").count()
    abroad_count = (
        scoped_students.join(StudyCase, StudyCase.student_id == Student.id)
        .filter(StudyCase.is_active.is_(True), StudyCase.status.in_(["parti", "arrive", "installe"]))
        .distinct()
        .count()
    )

    by_filiere = db_count(scoped_students, Student.filiere)
    by_niveau = db_count(scoped_students, Student.niveau)
    by_promotion = db_count(scoped_students, Student.promotion)

    q = request.args.get("q", "").strip()
    search_results = []
    if q:
        search_query = scope_query_by_branch(Student.query, Student)
        if selected_branch_id:
            search_query = search_query.filter(Student.branch_id == selected_branch_id)
        search_results = search_query.filter(
            (Student.nom.ilike(f"%{q}%"))
            | (Student.prenoms.ilike(f"%{q}%"))
            | (Student.matricule.ilike(f"%{q}%"))
        ).order_by(Student.nom.asc(), Student.prenoms.asc(), Student.matricule.asc()).limit(20).all()

    recent_students = scoped_students.order_by(Student.nom.asc(), Student.prenoms.asc(), Student.matricule.asc()).limit(8).all()
    recent_documents_query = scope_query_by_branch(StudentDocument.query.join(Student, StudentDocument.student_id == Student.id), Student)
    if selected_branch_id:
        recent_documents_query = recent_documents_query.filter(Student.branch_id == selected_branch_id)
    recent_documents = recent_documents_query.order_by(StudentDocument.created_at.desc()).limit(10).all()

    branch_rows = []
    for b in branch_filter_options:
        b_total = scope_query_by_branch(Student.query.filter(Student.branch_id == b.id), Student).count()
        b_abroad = (
            scope_query_by_branch(
                Student.query.join(StudyCase, StudyCase.student_id == Student.id).filter(Student.branch_id == b.id),
                Student,
            )
            .filter(
                StudyCase.is_active.is_(True),
                StudyCase.status.in_(["parti", "arrive", "installe"]),
            )
            .distinct()
            .count()
        )
        branch_rows.append({"id": b.id, "name": b.name, "country_code": b.country_code, "total": b_total, "abroad": b_abroad})

    dashboard_chart_data = {
        "kpi_labels": ["Actifs", "Suspendus", "Anciens", "A l'étranger"],
        "kpi_values": [actifs, suspendus, anciens, abroad_count],
        "filiere_labels": [x["label"] for x in by_filiere],
        "filiere_values": [x["value"] for x in by_filiere],
        "niveau_labels": [x["label"] for x in by_niveau],
        "niveau_values": [x["value"] for x in by_niveau],
        "promotion_labels": [x["label"] for x in by_promotion],
        "promotion_values": [x["value"] for x in by_promotion],
        "branch_labels": [f'{r["name"]} ({r["country_code"]})' for r in branch_rows],
        "branch_total": [r["total"] for r in branch_rows],
        "branch_abroad": [r["abroad"] for r in branch_rows],
    }

    it_subscription_alerts = None
    it_pending_review_rows = []
    it_billing_filters = None
    it_billing_summary = None
    it_billing_chart_data = None
    if is_super_admin_platform(current_user) and not it_agency_mode:
        it_ctx = _build_it_saas_context()
        it_subscription_alerts = it_ctx["it_subscription_alerts"]
        it_pending_review_rows = it_ctx["it_pending_review_rows"]
        it_billing_filters = it_ctx["it_billing_filters"]
        it_billing_summary = it_ctx["it_billing_summary"]
        it_billing_chart_data = it_ctx["it_billing_chart_data"]

    rdv_public_link = None
    rdv_event_slug = None
    if settings and settings.branch_id:
        rdv_event = Event.query.filter_by(slug=f"dashboard-rdv-{settings.branch_id}", branch_id=settings.branch_id).first()
        if rdv_event:
            rdv_event_slug = rdv_event.slug
            rdv_public_link = url_for("public_rdv.book_event", slug=rdv_event.slug, _external=True)


    return render_template(
        "dashboard/index.html",
        kpis={"total": total, "actifs": actifs, "suspendus": suspendus, "anciens": anciens, "abroad": abroad_count},
        by_filiere=by_filiere,
        by_niveau=by_niveau,
        by_promotion=by_promotion,
        recent_students=recent_students,
        recent_documents=recent_documents,
        search_results=search_results,
        q=q,
        branch_filter_options=branch_filter_options,
        selected_branch_id=selected_branch_id,
        branch_rows=branch_rows,
        settings=settings,
        settings_form=settings_form,
        dashboard_chart_data=dashboard_chart_data,
        it_agency_mode=it_agency_mode,
        it_subscription_alerts=it_subscription_alerts,
        it_pending_review_rows=it_pending_review_rows,
        it_billing_filters=it_billing_filters,
        it_billing_summary=it_billing_summary,
        it_billing_chart_data=it_billing_chart_data,
        rdv_public_link=rdv_public_link,
        rdv_event_slug=rdv_event_slug,
    )


@dashboard_bp.route("/dashboard")
@login_required
def index_alias():
    return redirect(url_for("dashboard.index"))


@dashboard_bp.route("/founder")
@login_required
@role_required("FOUNDER")
@plan_required("enterprise", "Rapports globaux")
def founder_dashboard():
    membership_rows = Membership.query.filter_by(user_id=current_user.id).all()
    allowed_branch_ids = sorted({m.branch_id for m in membership_rows if m.branch_id is not None})
    if not allowed_branch_ids and current_user.branch_id:
        allowed_branch_ids = [current_user.branch_id]

    selected_branch_id = request.args.get("branch_id", type=int) or 0
    branch_filter_options = Branch.query.filter(Branch.id.in_(allowed_branch_ids)).order_by(Branch.name.asc()).all() if allowed_branch_ids else []
    if selected_branch_id and selected_branch_id in set(allowed_branch_ids):
        allowed_branch_ids = [selected_branch_id]
    else:
        selected_branch_id = 0

    if not allowed_branch_ids:
        empty_chart = {
            "branch_labels": [],
            "branch_values": [],
            "status_labels": [],
            "status_values": [],
            "entity_labels": [],
            "entity_values": [],
            "agent_labels": [],
            "agent_values": [],
            "payment_split_labels": ["Paiements valides", "Paiements non valides"],
            "payment_split_values": [0, 0],
            "payment_branch_labels": [],
            "payment_branch_paid": [],
            "payment_branch_unpaid": [],
        }
        return render_template(
            "dashboard/founder.html",
            students_by_branch=[],
            students_unassigned=0,
            cases_by_status=[],
            cases_by_entity=[],
            cases_by_school=[],
            pending_commissions=0.0,
            paid_commissions=0.0,
            total_payments_amount=0.0,
            paid_payments_amount=0.0,
            unpaid_payments_amount=0.0,
            paid_payments_count=0,
            unpaid_payments_count=0,
            payments_by_branch_rows=[],
            recent_support=[],
            top_agents=[],
            country_branch_breakdown=[],
            founder_chart_data=empty_chart,
            branch_filter=selected_branch_id,
            branch_filter_options=branch_filter_options,
        )

    students_by_branch = (
        db.session.query(
            Branch.id,
            Branch.name,
            Branch.country_code,
            func.count(Student.id).label("total_students"),
        )
        .outerjoin(Student, and_(Student.branch_id == Branch.id, Student.deleted_at.is_(None)))
        .filter(Branch.id.in_(allowed_branch_ids))
        .group_by(Branch.id, Branch.name, Branch.country_code)
        .order_by(func.count(Student.id).desc(), Branch.name.asc())
        .all()
    )
    students_unassigned = 0

    cases_by_status = (
        db.session.query(StudyCase.status, func.count(StudyCase.id))
        .filter(StudyCase.branch_id.in_(allowed_branch_ids))
        .group_by(StudyCase.status)
        .order_by(StudyCase.status.asc())
        .all()
    )
    cases_by_entity = (
        db.session.query(Entity.name, func.count(StudyCase.id))
        .join(StudyCase, StudyCase.entity_id == Entity.id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids))
        .group_by(Entity.name)
        .order_by(func.count(StudyCase.id).desc())
        .all()
    )
    cases_by_school = (
        db.session.query(School.name, func.count(StudyCase.id))
        .join(StudyCase, StudyCase.school_id == School.id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids))
        .group_by(School.name)
        .order_by(func.count(StudyCase.id).desc())
        .limit(10)
        .all()
    )

    pending_commissions = (
        db.session.query(func.coalesce(func.sum(CommissionRecord.amount), 0.0))
        .join(StudyCase, StudyCase.id == CommissionRecord.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids), CommissionRecord.status == "pending")
        .scalar()
    )
    paid_commissions = (
        db.session.query(func.coalesce(func.sum(CommissionRecord.amount), 0.0))
        .join(StudyCase, StudyCase.id == CommissionRecord.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids), CommissionRecord.status == "paid")
        .scalar()
    )

    total_payments_amount = (
        db.session.query(func.coalesce(func.sum(CasePayment.amount), 0.0))
        .join(StudyCase, StudyCase.id == CasePayment.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids))
        .scalar()
        or 0.0
    )
    paid_payments_amount = (
        db.session.query(func.coalesce(func.sum(CasePayment.amount), 0.0))
        .join(StudyCase, StudyCase.id == CasePayment.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids), CasePayment.paid.is_(True))
        .scalar()
        or 0.0
    )
    unpaid_payments_amount = max(float(total_payments_amount) - float(paid_payments_amount), 0.0)
    paid_payments_count = (
        db.session.query(func.count(CasePayment.id))
        .join(StudyCase, StudyCase.id == CasePayment.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids), CasePayment.paid.is_(True))
        .scalar()
        or 0
    )
    unpaid_payments_count = (
        db.session.query(func.count(CasePayment.id))
        .join(StudyCase, StudyCase.id == CasePayment.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids), CasePayment.paid.is_(False))
        .scalar()
        or 0
    )

    payments_by_branch_rows = (
        db.session.query(
            Branch.name,
            func.coalesce(func.sum(CasePayment.amount), 0.0).label("total_amount"),
            func.coalesce(func.sum(case((CasePayment.paid.is_(True), CasePayment.amount), else_=0.0)), 0.0).label("paid_amount"),
            func.coalesce(func.sum(case((CasePayment.paid.is_(False), CasePayment.amount), else_=0.0)), 0.0).label("unpaid_amount"),
        )
        .outerjoin(StudyCase, StudyCase.branch_id == Branch.id)
        .outerjoin(CasePayment, CasePayment.case_id == StudyCase.id)
        .filter(Branch.id.in_(allowed_branch_ids))
        .group_by(Branch.id, Branch.name)
        .order_by(Branch.name.asc())
        .all()
    )

    recent_support = (
        ArrivalSupport.query.join(StudyCase, StudyCase.id == ArrivalSupport.case_id)
        .filter(StudyCase.branch_id.in_(allowed_branch_ids))
        .order_by(ArrivalSupport.created_at.desc())
        .limit(12)
        .all()
    )

    top_agents = (
        db.session.query(
            User.id,
            User.username,
            User.email,
            Branch.name,
            func.count(AuditLog.id).label("created_count"),
        )
        .join(AuditLog, AuditLog.user_id == User.id)
        .outerjoin(Branch, Branch.id == User.branch_id)
        .filter(AuditLog.action == "student_create", AuditLog.branch_id.in_(allowed_branch_ids))
        .group_by(User.id, User.username, User.email, Branch.name)
        .order_by(func.count(AuditLog.id).desc(), User.username.asc())
        .limit(15)
        .all()
    )

    student_country_rows = (
        db.session.query(
            Branch.id.label("branch_id"),
            Branch.name.label("branch_name"),
            Branch.country_code.label("country_code"),
            func.count(Student.id).label("students_total"),
            func.sum(case((Student.statut == "actif", 1), else_=0)).label("actifs"),
            func.sum(case((Student.statut == "suspendu", 1), else_=0)).label("suspendus"),
            func.sum(case((Student.statut == "ancien", 1), else_=0)).label("anciens"),
            func.sum(case((Student.statut_global == "prospect", 1), else_=0)).label("prospects"),
            func.sum(case((Student.statut_global == "en_procedure", 1), else_=0)).label("en_procedure"),
            func.sum(case((Student.statut_global == "parti", 1), else_=0)).label("partis"),
            func.sum(case((Student.statut_global == "sur_place", 1), else_=0)).label("sur_place"),
            func.sum(case((Student.statut_global == "termine", 1), else_=0)).label("termines"),
        )
        .outerjoin(Student, and_(Student.branch_id == Branch.id, Student.deleted_at.is_(None)))
        .filter(Branch.id.in_(allowed_branch_ids))
        .group_by(Branch.id, Branch.name, Branch.country_code)
        .order_by(func.count(Student.id).desc(), Branch.name.asc())
        .all()
    )

    case_country_rows = (
        db.session.query(
            Branch.id.label("branch_id"),
            func.sum(case((StudyCase.status == "nouveau", 1), else_=0)).label("c_nouveau"),
            func.sum(case((StudyCase.status == "dossier_en_cours", 1), else_=0)).label("c_dossier"),
            func.sum(case((StudyCase.status == "admission", 1), else_=0)).label("c_admission"),
            func.sum(case((StudyCase.status == "visa", 1), else_=0)).label("c_visa"),
            func.sum(case((StudyCase.status == "billet", 1), else_=0)).label("c_billet"),
            func.sum(case((StudyCase.status == "arrive", 1), else_=0)).label("c_arrive"),
            func.sum(case((StudyCase.status == "installe", 1), else_=0)).label("c_installe"),
            func.sum(case((StudyCase.status == "abandonne", 1), else_=0)).label("c_abandonne"),
        )
        .outerjoin(StudyCase, StudyCase.branch_id == Branch.id)
        .filter(Branch.id.in_(allowed_branch_ids))
        .group_by(Branch.id)
        .all()
    )
    case_country_map = {r.branch_id: r for r in case_country_rows}

    country_branch_breakdown = []
    for row in student_country_rows:
        case_row = case_country_map.get(row.branch_id)
        country_branch_breakdown.append(
            {
                "branch_name": row.branch_name,
                "country_code": row.country_code,
                "students_total": int(row.students_total or 0),
                "actifs": int(row.actifs or 0),
                "suspendus": int(row.suspendus or 0),
                "anciens": int(row.anciens or 0),
                "prospects": int(row.prospects or 0),
                "en_procedure": int(row.en_procedure or 0),
                "partis": int(row.partis or 0),
                "sur_place": int(row.sur_place or 0),
                "termines": int(row.termines or 0),
                "c_nouveau": int(getattr(case_row, "c_nouveau", 0) or 0),
                "c_dossier": int(getattr(case_row, "c_dossier", 0) or 0),
                "c_admission": int(getattr(case_row, "c_admission", 0) or 0),
                "c_visa": int(getattr(case_row, "c_visa", 0) or 0),
                "c_billet": int(getattr(case_row, "c_billet", 0) or 0),
                "c_arrive": int(getattr(case_row, "c_arrive", 0) or 0),
                "c_installe": int(getattr(case_row, "c_installe", 0) or 0),
                "c_abandonne": int(getattr(case_row, "c_abandonne", 0) or 0),
            }
        )

    founder_chart_data = {
        "branch_labels": [f"{r[1]} ({r[2]})" for r in students_by_branch],
        "branch_values": [int(r[3] or 0) for r in students_by_branch],
        "status_labels": [r[0] for r in cases_by_status],
        "status_values": [int(r[1] or 0) for r in cases_by_status],
        "entity_labels": [r[0] for r in cases_by_entity],
        "entity_values": [int(r[1] or 0) for r in cases_by_entity],
        "agent_labels": [a[1] for a in top_agents],
        "agent_values": [int(a[4] or 0) for a in top_agents],
        "payment_split_labels": ["Paiements valides", "Paiements non valides"],
        "payment_split_values": [round(float(paid_payments_amount), 2), round(float(unpaid_payments_amount), 2)],
        "payment_branch_labels": [r[0] for r in payments_by_branch_rows],
        "payment_branch_paid": [round(float(r[2] or 0), 2) for r in payments_by_branch_rows],
        "payment_branch_unpaid": [round(float(r[3] or 0), 2) for r in payments_by_branch_rows],
    }

    return render_template(
        "dashboard/founder.html",
        students_by_branch=students_by_branch,
        students_unassigned=students_unassigned,
        cases_by_status=cases_by_status,
        cases_by_entity=cases_by_entity,
        cases_by_school=cases_by_school,
        pending_commissions=pending_commissions,
        paid_commissions=paid_commissions,
        total_payments_amount=float(total_payments_amount or 0),
        paid_payments_amount=float(paid_payments_amount or 0),
        unpaid_payments_amount=float(unpaid_payments_amount or 0),
        paid_payments_count=int(paid_payments_count or 0),
        unpaid_payments_count=int(unpaid_payments_count or 0),
        payments_by_branch_rows=payments_by_branch_rows,
        recent_support=recent_support,
        top_agents=top_agents,
        country_branch_breakdown=country_branch_breakdown,
        founder_chart_data=founder_chart_data,
        branch_filter=selected_branch_id,
        branch_filter_options=branch_filter_options,
    )


@dashboard_bp.route("/arrival-support", methods=["GET", "POST"])

@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def arrival_support():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    cases_query = (
        scope_query_by_branch(StudyCase.query.filter(StudyCase.is_active.is_(True)), StudyCase)
        .join(Student, StudyCase.student_id == Student.id)
    )
    if q:
        cases_query = cases_query.filter(
            (Student.nom.ilike(f"%{q}%"))
            | (Student.prenoms.ilike(f"%{q}%"))
            | (Student.matricule.ilike(f"%{q}%"))
        )
    cases = cases_query.order_by(StudyCase.id.desc()).all()

    form = ArrivalSupportForm()
    form.case_id.choices = [
        (
            c.id,
            f"#{c.id} | {c.student.matricule if c.student else 'N/A'} | {(c.student.nom + ' ' + c.student.prenoms) if c.student else 'N/A'}",
        )
        for c in cases
    ]

    if form.validate_on_submit():
        case_row = StudyCase.query.get_or_404(form.case_id.data)
        scoped = scope_query_by_branch(StudyCase.query.filter(StudyCase.id == case_row.id), StudyCase).first()
        if not scoped:
            flash("Acces refuse pour ce dossier.", "danger")
            return redirect(url_for("dashboard.arrival_support"))

        row = ArrivalSupport.query.filter_by(case_id=case_row.id).first()
        if row is None:
            row = ArrivalSupport(case_id=case_row.id)
            db.session.add(row)

        row.host_entity_name = (form.host_entity_name.data or "").strip() or None
        row.contact_name = (form.contact_name.data or "").strip() or None
        row.phone = (form.phone.data or "").strip() or None
        row.email = (form.email.data or "").strip() or None
        row.lodging_status = form.lodging_status.data or None
        row.pickup_status = form.pickup_status.data or None
        row.mentor_assigned = (form.mentor_assigned.data or "").strip() or None
        row.followup_notes = (form.followup_notes.data or "").strip() or None
        row.confirmed_at = form.confirmed_at.data
        db.session.commit()
        add_audit_log(current_user.id, "arrival_support_upsert", f"Suivi arrivee dossier #{case_row.id}", student_id=case_row.student_id, branch_id=case_row.branch_id, action="arrival_support_upsert")
        flash("Suivi arrivee enregistré.", "success")
        return redirect(url_for("dashboard.arrival_support"))

    support_query = (
        db.session.query(ArrivalSupport, Student.matricule, Student.nom, Student.prenoms)
        .join(StudyCase, ArrivalSupport.case_id == StudyCase.id)
        .join(Student, StudyCase.student_id == Student.id)
    )
    support_query = scope_query_by_branch(support_query, StudyCase)
    if q:
        support_query = support_query.filter(
            (Student.nom.ilike(f"%{q}%"))
            | (Student.prenoms.ilike(f"%{q}%"))
            | (Student.matricule.ilike(f"%{q}%"))
        )
    support_pagination = support_query.order_by(ArrivalSupport.created_at.desc()).paginate(page=page, per_page=12, error_out=False)
    support_rows = [
        {
            "row": item[0],
            "matricule": item[1],
            "nom_complet": f"{item[2] or ''} {item[3] or ''}".strip() or "-",
        }
        for item in support_pagination.items
    ]

    return render_template(
        "dashboard/arrival_support.html",
        form=form,
        support_rows=support_rows,
        support_pagination=support_pagination,
        q=q,
    )


@dashboard_bp.route("/commissions", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "IT")
@plan_required("pro", "Suivi procedures et commissions")
def commissions():
    can_manage_commissions = normalized_role(current_user.role) == "FOUNDER"
    form = CommissionRuleForm()
    form.entity_id.choices = [(e.id, e.name) for e in Entity.query.order_by(Entity.name.asc()).all()]
    form.school_id.choices = [(0, "Toutes")] + [(s.id, s.name) for s in School.query.order_by(School.name.asc()).all()]

    if form.validate_on_submit() and can_manage_commissions:
        rule = CommissionRule(
            entity_id=form.entity_id.data,
            school_id=form.school_id.data or None,
            amount_per_student=form.amount_per_student.data,
            currency=(form.currency.data or "EUR").strip().upper(),
            trigger_status=form.trigger_status.data,
        )
        db.session.add(rule)
        db.session.commit()
        add_audit_log(current_user.id, "commission_rule_create", f"Regle commission #{rule.id}", branch_id=current_user.branch_id, action="commission_rule_create")
        flash("Regle commission ajoutee.", "success")
        return redirect(url_for("dashboard.commissions"))
    elif request.method == "POST" and not can_manage_commissions:
        flash("Lecture seule: seul le compte FOUNDER peut modifiér les regles de commission.", "warning")
        return redirect(url_for("dashboard.commissions"))

    rules = CommissionRule.query.order_by(CommissionRule.created_at.desc()).all()
    cases = scope_query_by_branch(StudyCase.query, StudyCase).all()
    _generate_commission_records(cases, rules)

    now = datetime.utcnow()
    selected_year = request.args.get("annee", type=int)
    if selected_year is None:
        selected_year = now.year
    selected_month = request.args.get("mois", type=int)
    if selected_month is None:
        selected_month = now.month
    if selected_month not in range(0, 13):
        selected_month = now.month

    records_query = CommissionRecord.query.join(StudyCase, CommissionRecord.case_id == StudyCase.id)
    scoped_case_ids = [c.id for c in cases]
    if scoped_case_ids:
        records_query = records_query.filter(StudyCase.id.in_(scoped_case_ids))
    else:
        records_query = records_query.filter(False)
    all_records = records_query.order_by(CommissionRecord.created_at.desc()).all()

    def _record_period_date(row):
        if row.status == "paid" and row.paid_at:
            return row.paid_at
        return row.created_at

    year_options = sorted({(_record_period_date(r).year if _record_period_date(r) else now.year) for r in all_records} | {now.year}, reverse=True)
    records = []
    for r in all_records:
        dt = _record_period_date(r)
        if dt is None:
            continue
        if selected_year and dt.year != selected_year:
            continue
        if selected_month and dt.month != selected_month:
            continue
        records.append(r)

    pending_total = sum(float(r.amount or 0) for r in records if r.status == "pending")
    paid_total = sum(float(r.amount or 0) for r in records if r.status == "paid")

    monthly_pending = [0.0] * 12
    monthly_paid = [0.0] * 12
    for r in all_records:
        dt = _record_period_date(r)
        if dt is None or dt.year != selected_year:
            continue
        month_idx = dt.month - 1
        amount = float(r.amount or 0)
        if r.status == "paid":
            monthly_paid[month_idx] += amount
        else:
            monthly_pending[month_idx] += amount

    entity_totals = {}
    for r in records:
        entity_name = "N/A"
        if r.study_case and r.study_case.entity:
            entity_name = r.study_case.entity.name
        entity_totals[entity_name] = entity_totals.get(entity_name, 0.0) + float(r.amount or 0)

    chart_data = {
        "split_labels": ["A recevoir", "Encaisse"],
        "split_values": [round(pending_total, 2), round(paid_total, 2)],
        "month_labels": ["Jan", "Fev", "Mar", "Avr", "Mai", "Jun", "Jul", "Aou", "Sep", "Oct", "Nov", "Dec"],
        "month_pending": [round(v, 2) for v in monthly_pending],
        "month_paid": [round(v, 2) for v in monthly_paid],
        "entity_labels": list(entity_totals.keys()),
        "entity_values": [round(v, 2) for v in entity_totals.values()],
    }

    pay_forms = {r.id: CommissionPayForm() for r in records if r.status == "pending"} if can_manage_commissions else {}
    return render_template(
        "dashboard/commissions.html",
        form=form,
        rules=rules,
        records=records,
        pending_total=pending_total,
        paid_total=paid_total,
        pay_forms=pay_forms,
        selected_month=selected_month,
        selected_year=selected_year,
        year_options=year_options,
        chart_data=chart_data,
        can_manage_commissions=can_manage_commissions,
    )


@dashboard_bp.route("/commissions/rules/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER")
def edit_commission_rule(rule_id):
    rule = CommissionRule.query.get_or_404(rule_id)
    form = CommissionRuleForm(obj=rule)
    form.entity_id.choices = [(e.id, e.name) for e in Entity.query.order_by(Entity.name.asc()).all()]
    form.school_id.choices = [(0, "Toutes")] + [(s.id, s.name) for s in School.query.order_by(School.name.asc()).all()]

    if form.validate_on_submit():
        rule.entity_id = form.entity_id.data
        rule.school_id = form.school_id.data or None
        rule.amount_per_student = form.amount_per_student.data
        rule.currency = (form.currency.data or "EUR").strip().upper()
        rule.trigger_status = form.trigger_status.data
        db.session.commit()
        add_audit_log(
            current_user.id,
            "commission_rule_update",
            f"Regle commission #{rule.id} modifiée",
            branch_id=current_user.branch_id,
            action="commission_rule_update",
        )
        flash("Regle commission modifiée.", "success")
        return redirect(url_for("dashboard.commissions"))

    return render_template("dashboard/commission_rule_edit.html", form=form, rule=rule)


@dashboard_bp.route("/commissions/rules/<int:rule_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER")
def delete_commission_rule(rule_id):
    rule = CommissionRule.query.get_or_404(rule_id)
    summary = f"{rule.entity.name if rule.entity else rule.entity_id} / {rule.school.name if rule.school else 'Toutes'}"
    db.session.delete(rule)
    db.session.commit()
    add_audit_log(
        current_user.id,
        "commission_rule_delete",
        f"Regle commission supprimée #{rule_id} ({summary})",
        branch_id=current_user.branch_id,
        action="commission_rule_delete",
    )
    flash("Regle commission supprimée.", "success")
    return redirect(url_for("dashboard.commissions"))


@dashboard_bp.route("/commissions/<int:record_id>/pay", methods=["POST"])
@login_required
@role_required("FOUNDER")
def mark_commission_paid(record_id):
    record = CommissionRecord.query.get_or_404(record_id)
    case_row = StudyCase.query.get(record.case_id)
    if case_row is None:
        flash("Dossier introuvable.", "danger")
        return redirect(url_for("dashboard.commissions"))

    scoped = scope_query_by_branch(StudyCase.query.filter(StudyCase.id == case_row.id), StudyCase).first()
    if not scoped:
        flash("Acces refuse.", "danger")
        return redirect(url_for("dashboard.commissions"))

    form = CommissionPayForm()
    if not form.validate_on_submit():
        flash("Date paiement invalide.", "danger")
        return redirect(url_for("dashboard.commissions"))

    record.status = "paid"
    record.paid_at = form.paid_at.data or db.func.now()
    db.session.commit()
    add_audit_log(current_user.id, "commission_paid", f"Commission #{record.id} payee", student_id=case_row.student_id, branch_id=case_row.branch_id, action="commission_paid")
    flash("Commission marquee payee.", "success")
    mois = request.form.get("mois", type=int)
    annee = request.form.get("annee", type=int)
    return redirect(url_for("dashboard.commissions", mois=mois, annee=annee))


def db_count(base_query, column):
    rows = base_query.with_entities(column, func.count(Student.id)).group_by(column).all()
    return [{"label": label or "N/A", "value": value} for label, value in rows]


def _generate_commission_records(cases, rules):
    if not cases or not rules:
        return
    changed = sync_commissions_for_cases(cases)
    if changed:
        db.session.commit()






















