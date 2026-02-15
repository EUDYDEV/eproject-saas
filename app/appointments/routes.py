from datetime import datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.appointments.forms import EventForm, EventTokenForm, SlotBookingForm
from app.extensions import db
from app.models import Booking, Branch, Event, EventSlot, InviteToken, SMTPSetting, Student
from app.utils.audit import add_audit_log
from app.utils.authz import can_access_branch, normalized_role, role_required, scope_query_by_branch, user_branch_ids
from app.utils.emailer import send_email_smtp
from app.utils.tokens import generate_token_value
from app.utils.subscriptions import plan_required


appointments_bp = Blueprint("appointments", __name__, url_prefix="/appointments")
public_rdv_bp = Blueprint("public_rdv", __name__)


def get_effective_smtp_settings(branch_id=None):
    if branch_id:
        settings = SMTPSetting.query.filter_by(branch_id=branch_id).first()
        if settings:
            return settings
        return None

    settings = SMTPSetting.query.filter_by(branch_id=None).first()
    if settings:
        return settings

    cfg = current_app.config
    required = [cfg.get("SMTP_HOST"), cfg.get("SMTP_USERNAME"), cfg.get("SMTP_PASSWORD"), cfg.get("SMTP_FROM")]
    if all(required):
        return SimpleNamespace(
            host=cfg["SMTP_HOST"],
            port=cfg["SMTP_PORT"],
            username=cfg["SMTP_USERNAME"],
            password=cfg["SMTP_PASSWORD"],
            from_email=cfg["SMTP_FROM"],
            use_tls=cfg["SMTP_TLS"],
        )
    return None


def _enforce_event_access(event):
    if not can_access_branch(event.branch_id):
        return False
    return True


def _available_slots(event):
    now = datetime.utcnow()
    slots = (
        EventSlot.query.filter_by(event_id=event.id)
        .filter(EventSlot.booked_count < EventSlot.capacity)
        .filter(EventSlot.start_datetime >= now)
        .order_by(EventSlot.start_datetime.asc())
        .all()
    )
    return slots


def _send_booking_confirmation(booking, event, slot):
    smtp = get_effective_smtp_settings(event.branch_id)
    if not smtp or not booking.email:
        return

    subject = f"Confirmation RDV - {event.title}"
    body_html = (
        f"<p>Bonjour {booking.name},</p>"
        f"<p>Votre rendez-vous est confirmé.</p>"
        f"<p><strong>Événement:</strong> {event.title}<br>"
        f"<strong>Date:</strong> {slot.start_datetime.strftime('%Y-%m-%d')}<br>"
        f"<strong>Heure:</strong> {slot.start_datetime.strftime('%H:%M')} - {slot.end_datetime.strftime('%H:%M')}<br>"
        f"<strong>Lieu:</strong> {event.location or 'À définir'}</p>"
        "<p>Merci.</p>"
    )
    body_text = (
        f"Bonjour {booking.name},\n"
        f"Votre rendez-vous est confirmé.\n"
        f"Événement: {event.title}\n"
        f"Date: {slot.start_datetime.strftime('%Y-%m-%d')}\n"
        f"Heure: {slot.start_datetime.strftime('%H:%M')} - {slot.end_datetime.strftime('%H:%M')}\n"
        f"Lieu: {event.location or 'À définir'}"
    )
    try:
        send_email_smtp(smtp, booking.email, subject, body_html, body_text)
    except Exception:
        return


@appointments_bp.route("/")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "RDV avances + tokens")
def list_appointments():
    selected_branch_id = request.args.get("branch_id", type=int) or 0

    if normalized_role(current_user.role) == "IT":
        branch_filter_options = Branch.query.order_by(Branch.name.asc()).all()
    else:
        scoped_ids = user_branch_ids(current_user)
        branch_filter_options = Branch.query.filter(Branch.id.in_(scoped_ids)).order_by(Branch.name.asc()).all() if scoped_ids else []

    allowed_branch_ids = {b.id for b in branch_filter_options}

    events_query = scope_query_by_branch(Event.query, Event)
    if selected_branch_id and selected_branch_id in allowed_branch_ids:
        events_query = events_query.filter(Event.branch_id == selected_branch_id)
    else:
        selected_branch_id = 0

    events = events_query.order_by(Event.start_date.desc(), Event.id.desc()).all()

    event_ids = [e.id for e in events]
    upcoming_bookings = []
    recent_confirmed_bookings = []
    if event_ids:
        upcoming_bookings = (
            Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id)
            .filter(EventSlot.event_id.in_(event_ids), EventSlot.start_datetime >= datetime.utcnow())
            .order_by(EventSlot.start_datetime.asc())
            .limit(200)
            .all()
        )
        recent_confirmed_bookings = (
            Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id)
            .filter(EventSlot.event_id.in_(event_ids), Booking.status == "confirmed")
            .order_by(Booking.created_at.desc())
            .limit(200)
            .all()
        )

    return render_template(
        "appointments/list.html",
        events=events,
        upcoming_bookings=upcoming_bookings,
        recent_confirmed_bookings=recent_confirmed_bookings,
        branch_filter=selected_branch_id,
        branch_filter_options=branch_filter_options,
    )

@appointments_bp.route("/bookings/<int:booking_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "RDV avances + tokens")
def delete_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    slot = EventSlot.query.get(booking.slot_id)
    if not slot:
        flash("Creneau introuvable.", "danger")
        return redirect(url_for("appointments.list_appointments"))

    event = Event.query.get(slot.event_id)
    if not event or not _enforce_event_access(event):
        return ("Forbidden", 403)

    if slot.booked_count and slot.booked_count > 0:
        slot.booked_count -= 1

    db.session.delete(booking)
    db.session.commit()
    add_audit_log(current_user.id, "booking_delete", f"Booking supprimé #{booking_id}", branch_id=event.branch_id, action="booking_delete")
    flash("Rendez-vous supprimé.", "success")

    next_url = request.form.get("next", "").strip()
    if next_url:
        return redirect(next_url)
    return redirect(url_for("appointments.list_appointments"))


@appointments_bp.route("/events/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
@plan_required("pro", "RDV avances + tokens")
def create_event():
    form = EventForm()

    if request.method == "GET":
        now = datetime.utcnow()
        form.start_date.data = now.date()
        form.end_date.data = (now + timedelta(days=7)).date()
        form.day_start_time.data = datetime.strptime("08:00", "%H:%M").time()
        form.day_end_time.data = datetime.strptime("17:00", "%H:%M").time()
        form.timezone.data = "Africa/Abidjan"
        form.max_per_day.data = 10
        form.slot_minutes.data = 30

    if form.validate_on_submit():
        if form.end_date.data < form.start_date.data:
            flash("La date de fin doit etre superieure ou egale a la date de debut.", "danger")
            return render_template("appointments/event_form.html", form=form, mode="create")
        if form.day_end_time.data <= form.day_start_time.data:
            flash("L'heure de fin doit etre apres l'heure de debut.", "danger")
            return render_template("appointments/event_form.html", form=form, mode="create")

        requested_branch_id = request.args.get("branch_id", type=int)
        branch_id = current_user.branch_id

        if requested_branch_id and can_access_branch(requested_branch_id):
            branch_id = requested_branch_id

        if branch_id is None:
            scoped_ids = user_branch_ids(current_user)
            if scoped_ids:
                branch_id = scoped_ids[0]

        if branch_id is None:
            default_branch = Branch.query.order_by(Branch.id.asc()).first()
            if default_branch is None:
                flash("Aucune branche disponible. Crée d'abord une branche avant de créer un événement.", "danger")
                return redirect(url_for("admin.branches_new"))
            branch_id = default_branch.id
            flash(f"Aucune branche selectionnee. Branche par defaut utilisee: {default_branch.name}.", "warning")

        row = Event(
            branch_id=branch_id,
            title=form.title.data.strip(),
            slug=form.slug.data.strip().lower(),
            description_html=(form.description_html.data or "").strip() or None,
            location=(form.location.data or "").strip() or None,
            timezone=(form.timezone.data or "").strip() or None,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            day_start_time=form.day_start_time.data,
            day_end_time=form.day_end_time.data,
            slot_minutes=form.slot_minutes.data,
            max_per_day=form.max_per_day.data,
            is_active=form.is_active.data == "1",
        )
        db.session.add(row)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Impossible de créer l'événement. Verifie le slug (unique) et les champs obligatoires.", "danger")
            return render_template("appointments/event_form.html", form=form, mode="create")
        add_audit_log(current_user.id, "event_create", f"Événement créée: {row.title}", branch_id=row.branch_id, action="event_create")
        flash("Événement créée.", "success")
        return redirect(url_for("appointments.view_event", event_id=row.id))

    return render_template("appointments/event_form.html", form=form, mode="create")

@appointments_bp.route("/events/<int:event_id>", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "RDV avances + tokens")
def view_event(event_id):
    event = Event.query.get_or_404(event_id)
    if not _enforce_event_access(event):
        return ("Forbidden", 403)

    form = EventForm(obj=event)
    form.is_active.data = "1" if event.is_active else "0"
    token_form = EventTokenForm()

    students = scope_query_by_branch(Student.query.filter(Student.email.isnot(None)), Student).order_by(Student.nom.asc()).all()
    token_form.student_id.choices = [(0, "Lien public (sans étudiant)")] + [(s.id, f"{s.matricule} - {s.nom} {s.prenoms}") for s in students]
    if request.method == "GET":
        token_form.expires_at.data = datetime.utcnow() + timedelta(days=7)

    if form.validate_on_submit():
        if form.end_date.data < form.start_date.data:
            flash("La date de fin doit etre superieure ou egale a la date de debut.", "danger")
            return redirect(url_for("appointments.view_event", event_id=event.id))
        if form.day_end_time.data <= form.day_start_time.data:
            flash("L'heure de fin doit etre apres l'heure de debut.", "danger")
            return redirect(url_for("appointments.view_event", event_id=event.id))

        event.title = form.title.data.strip()
        event.slug = form.slug.data.strip().lower()
        event.description_html = (form.description_html.data or "").strip() or None
        event.location = (form.location.data or "").strip() or None
        event.timezone = (form.timezone.data or "").strip() or None
        event.start_date = form.start_date.data
        event.end_date = form.end_date.data
        event.day_start_time = form.day_start_time.data
        event.day_end_time = form.day_end_time.data
        event.slot_minutes = form.slot_minutes.data
        event.max_per_day = form.max_per_day.data
        event.is_active = form.is_active.data == "1"
        db.session.commit()

        add_audit_log(current_user.id, "event_update", f"Événement modifié: {event.title}", branch_id=event.branch_id, action="event_update")
        flash("Événement mis a jour.", "success")
        return redirect(url_for("appointments.view_event", event_id=event.id))

    slots = EventSlot.query.filter_by(event_id=event.id).order_by(EventSlot.start_datetime.asc()).all()
    bookings = (
        Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id)
        .filter(EventSlot.event_id == event.id)
        .order_by(EventSlot.start_datetime.desc())
        .all()
    )
    invite_tokens = InviteToken.query.filter_by(event_id=event.id).order_by(InviteToken.created_at.desc()).limit(200).all()

    day_usage = {}
    for s in slots:
        key = s.start_datetime.date().isoformat()
        day_usage[key] = day_usage.get(key, 0) + s.booked_count

    public_link = url_for("public_rdv.book_event", slug=event.slug, _external=True)
    return render_template(
        "appointments/event_detail.html",
        event=event,
        form=form,
        slots=slots,
        bookings=bookings,
        token_form=token_form,
        invite_tokens=invite_tokens,
        day_usage=day_usage,
        public_link=public_link,
    )


@appointments_bp.route("/events/<int:event_id>/generate-slots", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
@plan_required("pro", "RDV avances + tokens")
def generate_slots(event_id):
    event = Event.query.get_or_404(event_id)
    if not _enforce_event_access(event):
        return ("Forbidden", 403)

    created = 0
    current_day = event.start_date
    while current_day <= event.end_date:
        slot_start = datetime.combine(current_day, event.day_start_time)
        day_end = datetime.combine(current_day, event.day_end_time)
        while slot_start + timedelta(minutes=event.slot_minutes) <= day_end:
            slot_end = slot_start + timedelta(minutes=event.slot_minutes)
            exists = EventSlot.query.filter_by(event_id=event.id, start_datetime=slot_start, end_datetime=slot_end).first()
            if not exists:
                db.session.add(
                    EventSlot(
                        event_id=event.id,
                        start_datetime=slot_start,
                        end_datetime=slot_end,
                        capacity=1,
                        booked_count=0,
                    )
                )
                created += 1
            slot_start = slot_end
        current_day = current_day + timedelta(days=1)

    db.session.commit()
    add_audit_log(current_user.id, "event_slots_generate", f"Slots generes: {created} (event #{event.id})", branch_id=event.branch_id, action="event_slots_generate")
    flash(f"Generation terminee: {created} nouveau(x) creneau(x).", "success")
    return redirect(url_for("appointments.view_event", event_id=event.id))


@appointments_bp.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
@plan_required("pro", "RDV avances + tokens")
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    if not _enforce_event_access(event):
        return ("Forbidden", 403)

    slot_ids = [s.id for s in EventSlot.query.filter_by(event_id=event.id).all()]
    if slot_ids:
        Booking.query.filter(Booking.slot_id.in_(slot_ids)).delete(synchronize_session=False)
    EventSlot.query.filter_by(event_id=event.id).delete(synchronize_session=False)
    InviteToken.query.filter_by(event_id=event.id).delete(synchronize_session=False)
    event_title = event.title
    branch_id = event.branch_id
    db.session.delete(event)
    db.session.commit()

    add_audit_log(current_user.id, "event_delete", f"Événement supprimé: {event_title}", branch_id=branch_id, action="event_delete")
    flash("Événement supprimé.", "success")
    return redirect(url_for("appointments.list_appointments"))


@appointments_bp.route("/events/<int:event_id>/tokens/new", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "RDV avances + tokens")
def generate_token(event_id):
    event = Event.query.get_or_404(event_id)
    if not _enforce_event_access(event):
        return ("Forbidden", 403)

    form = EventTokenForm()
    students = scope_query_by_branch(Student.query.filter(Student.email.isnot(None)), Student).order_by(Student.nom.asc()).all()
    form.student_id.choices = [(0, "Lien public (sans étudiant)")] + [(s.id, f"{s.matricule} - {s.nom} {s.prenoms}") for s in students]

    if not form.validate_on_submit():
        flash("Generation token invalide.", "danger")
        return redirect(url_for("appointments.view_event", event_id=event.id))

    student_id = form.student_id.data or None
    if student_id == 0:
        student_id = None

    token_value = generate_token_value()
    row = InviteToken(
        event_id=event.id,
        student_id=student_id,
        token=token_value,
        expires_at=form.expires_at.data,
    )
    db.session.add(row)
    db.session.commit()

    link = url_for("public_rdv.book_event", slug=event.slug, t=row.token, _external=True)
    add_audit_log(current_user.id, "event_token_create", f"Token créée pour event #{event.id}", student_id=student_id, branch_id=event.branch_id, action="event_token_create")
    flash(f"Lien token genere: {link}", "success")
    return redirect(url_for("appointments.view_event", event_id=event.id))


@appointments_bp.route("/request/<token_value>")
def request_appointment(token_value):
    token_row = InviteToken.query.filter_by(token=token_value).first()
    if not token_row:
        return render_template("forms/public_expired.html")
    event = Event.query.get(token_row.event_id)
    if not event:
        return render_template("forms/public_expired.html")
    return redirect(url_for("public_rdv.book_event", slug=event.slug, t=token_value))


@public_rdv_bp.route("/rdv/<slug>", methods=["GET", "POST"])
def book_event(slug):
    event = Event.query.filter_by(slug=slug, is_active=True).first_or_404()
    token_value = request.args.get("t", "").strip()

    invite = None
    student = None
    if token_value:
        invite = InviteToken.query.filter_by(token=token_value, event_id=event.id).first()
        if not invite:
            return render_template("forms/public_expired.html")

        if invite.used_at is not None:
            existing_booking = Booking.query.filter_by(invite_token_id=invite.id).order_by(Booking.id.desc()).first()
            if existing_booking:
                existing_slot = EventSlot.query.get(existing_booking.slot_id)
                if existing_slot:
                    return render_template("appointments/public_done.html", event=event, slot=existing_slot, booking=existing_booking)
            return render_template("forms/public_expired.html")

        if invite.expires_at < datetime.utcnow():
            return render_template("forms/public_expired.html")

        if invite.student_id:
            student = Student.query.get(invite.student_id)

    slots = _available_slots(event)
    slot_choices = [(s.id, f"{s.start_datetime.strftime('%Y-%m-%d %H:%M')} - {s.end_datetime.strftime('%H:%M')}") for s in slots]

    form = SlotBookingForm()
    form.slot_id.choices = slot_choices

    if student and request.method == "GET":
        form.name.data = f"{student.prenoms} {student.nom}".strip()
        form.email.data = student.email or ""
        form.phone.data = student.telephone or ""

    if form.validate_on_submit():
        slot = EventSlot.query.filter_by(id=form.slot_id.data, event_id=event.id).first()
        if not slot:
            flash("Creneau invalide.", "danger")
            return redirect(request.url)

        if slot.booked_count >= slot.capacity:
            flash("Ce creneau est deja complet.", "danger")
            return redirect(request.url)

        day_start = datetime.combine(slot.start_datetime.date(), datetime.min.time())
        day_end = day_start + timedelta(days=1)
        day_count = (
            Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id)
            .filter(
                EventSlot.event_id == event.id,
                EventSlot.start_datetime >= day_start,
                EventSlot.start_datetime < day_end,
                Booking.status.in_(["pending", "confirmed"]),
            )
            .count()
        )
        if day_count >= event.max_per_day:
            flash(f"Journee complete: {event.max_per_day} RDV atteints.", "danger")
            return redirect(request.url)

        booking = Booking(
            slot_id=slot.id,
            student_id=student.id if student else None,
            invite_token_id=invite.id if invite else None,
            name=form.name.data.strip(),
            email=form.email.data.strip().lower(),
            phone=(form.phone.data or "").strip() or None,
            status="confirmed",
        )
        db.session.add(booking)

        slot.booked_count += 1

        if student:
            student.email = booking.email
            student.telephone = booking.phone

        if invite:
            invite.used_at = datetime.utcnow()

        db.session.commit()
        _send_booking_confirmation(booking, event, slot)
        return render_template("appointments/public_done.html", event=event, slot=slot, booking=booking)

    day_slots = {}
    for slot in slots:
        key = slot.start_datetime.strftime("%Y-%m-%d")
        day_slots.setdefault(key, []).append(slot)

    return render_template("appointments/public_booking.html", event=event, form=form, day_slots=day_slots, token_value=token_value)














