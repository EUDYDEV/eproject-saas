import os
from datetime import datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from jinja2 import Template
from sqlalchemy import func

from app.emails.forms import DirectEmailForm, EmailSendForm, EmailTemplateForm, OrientationInviteForm, SMTPForm
from app.extensions import db
from app.models import (
    AgencySubscription,
    Branch,
    EmailDispatch,
    EmailLog,
    EmailTemplate,
    Entity,
    Event,
    Guardian,
    InviteToken,
    PortalSetting,
    SMTPSetting,
    School,
    Student,
    StudyCase,
)
from app.utils.audit import add_audit_log
from app.utils.authz import is_super_admin_platform, normalized_role, role_required, scope_query_by_branch, user_branch_ids
from app.utils.emailer import send_email_smtp
from app.utils.files import save_uploaded_file
from app.utils.tokens import generate_token_value
from app.utils.subscriptions import plan_required


emails_bp = Blueprint("emails", __name__, url_prefix="/emails")


def _smtp_default_data():
    return {
        "host": "",
        "port": 587,
        "use_tls": True,
        "username": "",
        "password": "",
        "from_email": "",
    }


def _resolve_sender_branch_id():
    if not current_user.is_authenticated:
        return None
    role = normalized_role(getattr(current_user, "role", None))
    if role in ("ADMIN_BRANCH", "EMPLOYEE"):
        return current_user.branch_id
    if role == "FOUNDER":
        if current_user.branch_id:
            return current_user.branch_id
        owner_sub = AgencySubscription.query.filter_by(owner_user_id=current_user.id).first()
        if owner_sub and owner_sub.branch_id:
            current_user.branch_id = owner_sub.branch_id
            db.session.commit()
            return owner_sub.branch_id

    return None


def get_effective_smtp_settings(branch_id=None):
    target_branch = branch_id
    if target_branch is None and current_user.is_authenticated:
        role = normalized_role(getattr(current_user, "role", None))
        if role in ("ADMIN_BRANCH", "FOUNDER", "EMPLOYEE"):
            target_branch = _resolve_sender_branch_id()

    if not target_branch:
        return None

    return SMTPSetting.query.filter_by(branch_id=target_branch).first()


def _base_email_context(item, overrides=None):
    branch_id = item.get("branch_id")
    settings = PortalSetting.query.filter_by(branch_id=branch_id).first() if branch_id else None
    branch = Branch.query.get(branch_id) if branch_id else None
    context = {
        "nom": item.get("nom", ""),
        "prenoms": item.get("prenoms", ""),
        "matricule": item.get("matricule", ""),
        "filiere": item.get("filiere", ""),
        "niveau": item.get("niveau", ""),
        "promotion": item.get("promotion", ""),
        "destination_country": item.get("destination_country", ""),
        "entity": item.get("entity", ""),
        "school": item.get("school", ""),
        "lien_formulaire": item.get("lien_formulaire", ""),
        "event_title": settings.event_title if settings else "",
        "event_date": settings.orientation_date if settings else "",
        "event_address": settings.orientation_address if settings else "",
        "event_phone": settings.orientation_phone if settings else "",
        "event_email": "",
        "representative_name": settings.representative_name if settings else "",
        "lien_rdv": "",
        "agency_name": branch.name if branch else "",
        "is_parent": item.get("recipient_type") == "guardian",
        "is_student": item.get("recipient_type") == "student",
        "is_abroad_student": bool(item.get("is_abroad_student")),
    }
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                context[k] = v

    return context


def _resolve_event_for_branch(branch_id):
    if not branch_id:
        return None
    today = datetime.utcnow().date()
    event = (
        Event.query.filter_by(branch_id=branch_id, is_active=True)
        .filter(Event.end_date >= today)
        .order_by(Event.start_date.asc(), Event.id.desc())
        .first()
    )
    if event:
        return event
    return (
        Event.query.filter_by(branch_id=branch_id, is_active=True)
        .order_by(Event.id.desc())
        .first()
    )


def _missing_rdv_event_branches(recipients):
    missing = []
    seen = set()
    for it in recipients:
        bid = it.get("branch_id")
        if not bid or bid in seen:
            continue
        seen.add(bid)
        if _resolve_event_for_branch(bid) is None:
            b = Branch.query.get(bid)
            missing.append(b.name if b else f"Branche #{bid}")
    return missing


def _build_rdv_link(item, branch_id):
    event = _resolve_event_for_branch(branch_id)
    if not event:
        return ""

    student_id = item.get("student_id") or None
    try:
        token_value = generate_token_value()
        invite = InviteToken(
            event_id=event.id,
            student_id=student_id,
            token=token_value,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        db.session.add(invite)
        db.session.flush()
        return url_for("public_rdv.book_event", slug=event.slug, t=token_value, _external=True)
    except Exception:
        # Fallback generic public booking link if token generation fails.
        return url_for("public_rdv.book_event", slug=event.slug, _external=True)


def _resolve_logo(logo_choice, branch_id=None, custom_logo_urls=None, logo_display="single"):
    if logo_choice == "none":
        return "", "", []

    logo_urls = []

    branch = Branch.query.get(branch_id) if branch_id else None
    if branch and branch.logo_url:
        logo_urls.append(branch.logo_url)

    if current_user.is_authenticated and is_super_admin_platform(current_user):
        settings = PortalSetting.query.first()
        platform_logo = (settings.site_logo_url or "").strip() if settings else ""
        if platform_logo:
            logo_urls.append(platform_logo)

    for u in (custom_logo_urls or []):
        val = (u or "").strip()
        if val:
            logo_urls.append(val)

    # Deduplicate while preserving order.
    seen = set()
    deduped = []
    for u in logo_urls:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    logo_urls = deduped

    if not logo_urls:
        return "", "", []

    display = (logo_display or "single").strip().lower()
    if display == "single":
        selected = logo_urls[:1]
    elif display == "two":
        selected = logo_urls[:2]
    elif display == "three":
        selected = logo_urls[:3]
    else:
        selected = logo_urls

    primary = selected[0] if selected else ""
    return primary, "", selected


def _upload_custom_logos(file_list):
    paths = []
    allowed_logo_ext = {"png", "jpg", "jpeg"}
    for file_storage in file_list:
        if not file_storage or not getattr(file_storage, "filename", ""):
            continue
        filename = (file_storage.filename or "").lower()
        ext = filename.rsplit(".", 1)[1] if "." in filename else ""
        if ext not in allowed_logo_ext:
            raise ValueError("Logo invalide. Utilise uniquement PNG/JPG/JPEG.")
        upload_dir = os.path.join(current_app.root_path, "static", "uploads", "email_logos")
        stored_name = save_uploaded_file(file_storage, upload_dir, allowed_logo_ext)
        paths.append(os.path.join(upload_dir, stored_name))
    return paths


def _wrap_email_html(content_html, logo_url, logo_text, cta_label="", cta_link="", logo_urls=None, logo_cids=None, footer_text=""):
    logo_urls = logo_urls or []
    logo_cids = logo_cids or []
    footer_logo_block = ""
    logo_img_style = (
        "width:auto;height:auto;max-width:96px;max-height:34px;border:0;display:block;"
    )

    def _logos_table(img_tags):
        cells = "".join(
            f"<td align='center' valign='middle' style='padding:0 6px;'>{tag}</td>"
            for tag in img_tags
        )
        return (
            "<tr><td align='center' style='padding-top:14px;padding-bottom:4px;'>"
            "<table role='presentation' cellpadding='0' cellspacing='0' border='0'>"
            f"<tr>{cells}</tr>"
            "</table>"
            "</td></tr>"
        )

    if logo_cids:
        img_tags = [
            f"<img src='cid:{cid}' alt='logo' style='{logo_img_style}'>"
            for cid in logo_cids
        ]
        footer_logo_block = _logos_table(img_tags)
    elif logo_urls:
        img_tags = [
            f"<img src='{u}' alt='logo' style='{logo_img_style}'>"
            for u in logo_urls
        ]
        footer_logo_block = _logos_table(img_tags)
    elif logo_url:
        footer_logo_block = (
            f"<tr><td align='center' style='padding-top:14px;padding-bottom:4px;'>"
            f"<img src='{logo_url}' alt='logo' style='{logo_img_style};margin:0 auto;'></td></tr>"
        )
    elif logo_text:
        footer_logo_block = f"<tr><td align='center' style='padding-top:14px;padding-bottom:4px;font-size:14px;font-weight:700;color:#0a3c8c;'>{logo_text}</td></tr>"

    cta_block = ""
    if cta_label and cta_link:
        cta_block = (
            "<tr><td align='center' style='padding-top:18px;'>"
            f"<a href='{cta_link}' style='background:#0d6efd;color:#ffffff;text-decoration:none;padding:12px 20px;border-radius:6px;display:inline-block;font-weight:600;'>{cta_label}</a>"
            "</td></tr>"
        )

    footer_block = (
        f"<tr><td style='padding-top:20px;font-size:12px;color:#6b7280;'>{footer_text}</td></tr>"
        if footer_text
        else ""
    )
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#f4f6f8;padding:20px 0;'>"
        "<tr><td align='center'>"
        "<table role='presentation' width='640' cellpadding='0' cellspacing='0' style='max-width:640px;background:#ffffff;border-radius:8px;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1f2937;'>"
        f"<tr><td style='font-size:15px;line-height:1.6;'>{content_html}</td></tr>"
        f"{cta_block}"
        f"{footer_logo_block}"
        f"{footer_block}"
        "</table>"
        "</td></tr></table>"
    )


def _populate_filter_choices(form):
    branch_choices = [(0, "Toutes")]
    role = normalized_role(current_user.role)
    if role == "IT":
        scoped_branches = Branch.query.order_by(Branch.name.asc()).all()
    else:
        allowed_ids = user_branch_ids(current_user)
        if allowed_ids:
            scoped_branches = Branch.query.filter(Branch.id.in_(allowed_ids)).order_by(Branch.name.asc()).all()
        else:
            scoped_branches = []
    branch_choices.extend([(b.id, b.name) for b in scoped_branches])

    entity_choices = [(0, "Toutes")]
    entity_choices.extend([(e.id, e.name) for e in Entity.query.order_by(Entity.name.asc()).all()])

    school_choices = [(0, "Toutes")]
    school_choices.extend([(s.id, s.name) for s in School.query.order_by(School.name.asc()).all()])

    if hasattr(form, "branch_id"):
        form.branch_id.choices = branch_choices
    if hasattr(form, "entity_id"):
        form.entity_id.choices = entity_choices
    if hasattr(form, "school_id"):
        form.school_id.choices = school_choices


def _student_base_query(
    branch_id=0,
    filiere="",
    niveau="",
    promotion="",
    statut="",
    case_status="",
    entity_id=0,
    school_id=0,
    abroad_only=False,
    exclude_abroad=False,
):
    query = scope_query_by_branch(Student.query, Student)

    if branch_id:
        query = query.filter(Student.branch_id == branch_id)
    if filiere:
        query = query.filter(Student.filiere == filiere.strip())
    if niveau:
        query = query.filter(Student.niveau == niveau.strip())
    if promotion:
        query = query.filter(Student.promotion == promotion.strip())
    if statut:
        query = query.filter(Student.statut == statut.strip())

    if case_status or entity_id or school_id:
        query = query.join(StudyCase, StudyCase.student_id == Student.id)
        if case_status:
            query = query.filter(StudyCase.status == case_status.strip())
        if entity_id:
            query = query.filter(StudyCase.entity_id == entity_id)
        if school_id:
            query = query.filter(StudyCase.school_id == school_id)

    if abroad_only:
        query = query.join(StudyCase, StudyCase.student_id == Student.id).filter(
            StudyCase.is_active.is_(True),
            StudyCase.status.in_(["parti", "arrive", "installe"]),
            StudyCase.created_at >= Student.created_at,
        )
    elif exclude_abroad:
        abroad_exists = (
            db.session.query(StudyCase.id)
            .filter(
                StudyCase.student_id == Student.id,
                StudyCase.is_active.is_(True),
                StudyCase.status.in_(["parti", "arrive", "installe"]),
                StudyCase.created_at >= Student.created_at,
            )
            .exists()
        )
        query = query.filter(
            ~abroad_exists
        )

    return query.distinct()


def _recipient_debug_counts(
    branch_id=0,
    filiere="",
    niveau="",
    promotion="",
    statut="",
    case_status="",
    entity_id=0,
    school_id=0,
    abroad_only=False,
    exclude_abroad=False,
):
    base_q = _student_base_query(
        branch_id,
        filiere,
        niveau,
        promotion,
        statut,
        case_status,
        entity_id,
        school_id,
        abroad_only=abroad_only,
        exclude_abroad=exclude_abroad,
    )
    total_students = base_q.count()
    students_with_email = base_q.filter(
        Student.email.isnot(None),
        func.length(func.trim(Student.email)) > 0,
    ).count()
    return total_students, students_with_email


def resolve_recipients(target, branch_id=0, filiere="", niveau="", promotion="", statut="", case_status="", entity_id=0, school_id=0):
    abroad_only = target == "abroad_students"
    exclude_abroad = target in ("students", "mix", "guardians")
    student_query = _student_base_query(
        branch_id,
        filiere,
        niveau,
        promotion,
        statut,
        case_status,
        entity_id,
        school_id,
        abroad_only=abroad_only,
        exclude_abroad=exclude_abroad,
    )
    students = student_query.order_by(Student.id.asc()).all()
    students_by_id = {s.id: s for s in students}

    items = []
    include_students = target in ("students", "abroad_students", "mix")
    include_guardians = target in ("guardians", "mix")

    if include_students:
        for s in students:
            if not s.email:
                continue
            active_case = (
                StudyCase.query.filter(
                    StudyCase.student_id == s.id,
                    StudyCase.is_active.is_(True),
                    StudyCase.created_at >= s.created_at,
                )
                .order_by(StudyCase.id.desc())
                .first()
            )
            items.append(
                {
                    "recipient_type": "student",
                    "email": s.email,
                    "student_id": s.id,
                    "nom": s.nom,
                    "prenoms": s.prenoms,
                    "matricule": s.matricule,
                    "filiere": s.filiere,
                    "niveau": s.niveau,
                    "promotion": s.promotion,
                    "destination_country": active_case.destination_country if active_case else "",
                    "entity": active_case.entity.name if active_case and active_case.entity else "",
                    "school": active_case.school.name if active_case and active_case.school else "",
                    "branch_id": s.branch_id,
                    "is_abroad_student": bool(active_case and active_case.status in ("parti", "arrive", "installe")),
                }
            )

    if include_guardians:
        student_ids = list(students_by_id.keys())
        if student_ids:
            guardians = Guardian.query.filter(Guardian.student_id.in_(student_ids)).order_by(Guardian.id.asc()).all()
            for g in guardians:
                s = students_by_id.get(g.student_id)
                if not s or not g.email:
                    continue
                active_case = (
                    StudyCase.query.filter(
                        StudyCase.student_id == s.id,
                        StudyCase.is_active.is_(True),
                        StudyCase.created_at >= s.created_at,
                    )
                    .order_by(StudyCase.id.desc())
                    .first()
                )
                items.append(
                    {
                        "recipient_type": "guardian",
                        "email": g.email,
                        "guardian_id": g.id,
                        "student_id": s.id,
                        "nom": g.nom,
                        "prenoms": g.prenoms,
                        "matricule": s.matricule,
                        "filiere": s.filiere,
                        "niveau": s.niveau,
                        "promotion": s.promotion,
                        "destination_country": active_case.destination_country if active_case else "",
                        "entity": active_case.entity.name if active_case and active_case.entity else "",
                        "school": active_case.school.name if active_case and active_case.school else "",
                        "branch_id": s.branch_id,
                        "is_abroad_student": bool(active_case and active_case.status in ("parti", "arrive", "installe")),
                    }
                )

    unique = []
    seen = set()
    for it in items:
        key = (it["recipient_type"], it["email"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    return unique


def resolve_students_recipients_unscoped(abroad_only=False, exclude_abroad=False):
    items = []
    query = Student.query.filter(
        Student.email.isnot(None),
        func.length(func.trim(Student.email)) > 0,
    )
    if abroad_only:
        query = query.join(StudyCase, StudyCase.student_id == Student.id).filter(
            StudyCase.is_active.is_(True),
            StudyCase.status.in_(["parti", "arrive", "installe"]),
            StudyCase.created_at >= Student.created_at,
        )
    elif exclude_abroad:
        abroad_exists = (
            db.session.query(StudyCase.id)
            .filter(
                StudyCase.student_id == Student.id,
                StudyCase.is_active.is_(True),
                StudyCase.status.in_(["parti", "arrive", "installe"]),
                StudyCase.created_at >= Student.created_at,
            )
            .exists()
        )
        query = query.filter(
            ~abroad_exists
        )
    students = query.order_by(Student.id.asc()).distinct().all()
    for s in students:
        active_case = (
            StudyCase.query.filter(
                StudyCase.student_id == s.id,
                StudyCase.is_active.is_(True),
                StudyCase.created_at >= s.created_at,
            )
            .order_by(StudyCase.id.desc())
            .first()
        )
        items.append(
            {
                "recipient_type": "student",
                "email": (s.email or "").strip().lower(),
                "student_id": s.id,
                "nom": s.nom,
                "prenoms": s.prenoms,
                "matricule": s.matricule,
                "filiere": s.filiere,
                "niveau": s.niveau,
                "promotion": s.promotion,
                "destination_country": active_case.destination_country if active_case else "",
                "entity": active_case.entity.name if active_case and active_case.entity else "",
                "school": active_case.school.name if active_case and active_case.school else "",
                "branch_id": s.branch_id,
            }
        )
    return items


@emails_bp.route("/smtp", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "IT")
@plan_required("pro", "Emails personnalises + logos")
def smtp_settings():
    role = normalized_role(current_user.role)
    target_branch = _resolve_sender_branch_id() if role == "FOUNDER" else None
    settings = SMTPSetting.query.filter_by(branch_id=target_branch).first()
    smtp_scope_label = "SMTP Plateforme IT (SaaS)"
    if target_branch:
        branch = Branch.query.get(target_branch)
        smtp_scope_label = f"SMTP Agence: {branch.name if branch else ('Branche #' + str(target_branch))}"

    if settings is None:
        form = SMTPForm(data=_smtp_default_data())
    else:
        form = SMTPForm(obj=settings)

    if form.validate_on_submit():
        if role == "FOUNDER" and not target_branch:
            flash("Impossible de determiner la branche agence pour ce compte. Contacte IT.", "danger")
            return redirect(url_for("emails.smtp_settings"))
        if settings is None:
            settings = SMTPSetting(updated_by=current_user.id, branch_id=target_branch)
            db.session.add(settings)
        settings.host = form.host.data.strip()
        settings.port = form.port.data
        settings.username = form.username.data.strip()
        settings.password = form.password.data.strip()
        settings.from_email = form.from_email.data.strip().lower()
        settings.use_tls = form.use_tls.data
        settings.updated_by = current_user.id
        settings.branch_id = target_branch
        db.session.commit()
        add_audit_log(current_user.id, "smtp_update", "SMTP settings updated", branch_id=current_user.branch_id, action="smtp_update")
        flash("Parametres SMTP sauvegardes.", "success")
        return redirect(url_for("emails.smtp_settings"))
    return render_template("emails/smtp.html", form=form, smtp_scope_label=smtp_scope_label)


@emails_bp.route("/templates")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
@plan_required("pro", "Emails personnalises + logos")
def template_list():
    templates = scope_query_by_branch(EmailTemplate.query, EmailTemplate).order_by(EmailTemplate.id.desc()).all()
    return render_template("emails/templates_list.html", templates=templates)


@emails_bp.route("/templates/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
@plan_required("pro", "Emails personnalises + logos")
def template_new():
    form = EmailTemplateForm()
    if form.validate_on_submit():
        role = normalized_role(current_user.role)
        tpl = EmailTemplate(
            name=form.name.data.strip(),
            subject=form.subject.data.strip(),
            body_html=form.body_html.data,
            body_text=form.body_text.data,
            created_by=current_user.id,
            branch_id=None if role == "IT" else current_user.branch_id,
        )
        db.session.add(tpl)
        db.session.commit()
        add_audit_log(current_user.id, "email_template_create", f"Template {tpl.name}", branch_id=current_user.branch_id, action="email_template_create")
        flash("Template cree.", "success")
        return redirect(url_for("emails.template_list"))
    return render_template("emails/template_form.html", form=form)


def _send_to_recipients(
    recipients,
    subject_tpl,
    body_html_tpl,
    body_text_tpl="",
    logo_choice="innov",
    logo_display="single",
    cta_label="",
    template_id=None,
    context_overrides=None,
    custom_logo_paths=None,
):
    sent_count = 0
    total = len(recipients)
    effective_cta = cta_label.strip() if cta_label else ""
    logo_cids = [f"logo_{idx}" for idx, _ in enumerate(custom_logo_paths or [], start=1)]
    inline_images = [{"cid": cid, "path": path} for cid, path in zip(logo_cids, custom_logo_paths or [])]
    sender_role = normalized_role(getattr(current_user, "role", None))
    sender_branch_id = _resolve_sender_branch_id() if sender_role in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE") else None

    for item in recipients:
        effective_branch_id = item.get("branch_id")
        if effective_branch_id is None and sender_branch_id:
            effective_branch_id = sender_branch_id

        context = _base_email_context(item, overrides=context_overrides)
        if effective_cta:
            rdv_link = _build_rdv_link(item, effective_branch_id)
            if rdv_link:
                context["lien_rdv"] = rdv_link
        subject = Template(subject_tpl or "").render(**context)
        raw_html = Template(body_html_tpl or "").render(**context)
        body_text = Template(body_text_tpl or "").render(**context)

        logo_url, logo_text, resolved_logo_urls = _resolve_logo(
            logo_choice,
            branch_id=item.get("branch_id"),
            custom_logo_urls=[],
            logo_display=logo_display,
        )
        cta_link = context.get("lien_rdv", "")
        html_wrapped = _wrap_email_html(
            raw_html,
            logo_url,
            logo_text,
            cta_label=effective_cta,
            cta_link=cta_link,
            logo_urls=resolved_logo_urls,
            logo_cids=logo_cids if logo_choice == "custom" else [],
            footer_text=context.get("agency_name") or "",
        )

        dispatch = EmailDispatch(
            branch_id=effective_branch_id,
            template_id=template_id,
            recipient_type=item["recipient_type"],
            recipient_email=item["email"],
            student_id=item.get("student_id"),
            guardian_id=item.get("guardian_id"),
            status="pending",
        )
        db.session.add(dispatch)
        db.session.flush()

        log = EmailLog(
            branch_id=effective_branch_id,
            to_email=item["email"],
            subject=subject,
            status="pending",
            sent_by=current_user.id,
        )
        db.session.add(log)

        try:
            smtp = get_effective_smtp_settings(effective_branch_id)
            if not smtp:
                raise RuntimeError(
                    f"SMTP agence introuvable (branch_id={effective_branch_id}). Configure SMTP dans cette agence avant envoi."
                )
            send_email_smtp(smtp, item["email"], subject, html_wrapped, body_text, inline_images=inline_images)
            dispatch.status = "sent"
            dispatch.sent_at = datetime.utcnow()
            log.status = "sent"
            sent_count += 1
        except Exception as exc:
            error_msg = str(exc)
            dispatch.status = "failed"
            dispatch.error_message = error_msg
            log.status = "failed"
            log.error = error_msg

    db.session.commit()
    return sent_count, total, ""


def _orientation_template_html():
    return (
        "<p>Bonjour {{ prenoms or '' }} {{ nom or '' }},</p>"
        "<p>{{ intro_message or '' }}</p>"
        "<p><strong>Informations événement :</strong><br>"
        "<strong>Événement :</strong> {{ event_title or '' }}<br>"
        "<strong>Date :</strong> {{ event_date or '' }}<br>"
        "<strong>Adresse :</strong> {{ event_address or '' }}<br>"
        "<strong>Contacts :</strong> {{ event_phone or '' }}<br>"
        "<strong>Email :</strong> {{ event_email or '' }}<br>"
        "<strong>Représentant :</strong> {{ representative_name or '' }}</p>"
        "<p>Merci de confirmer votre RDV en cliquant sur le bouton ci-dessous.</p>"
    )


def _orientation_template_text():
    return (
        "Bonjour {{ prenoms or '' }} {{ nom or '' }},\n\n"
        "{{ intro_message or '' }}\n\n"
        "Informations événement :\n"
        "Événement : {{ event_title or '' }}\n"
        "Date: {{ event_date or '' }}\n"
        "Adresse: {{ event_address or '' }}\n"
        "Contacts: {{ event_phone or '' }}\n"
        "Email: {{ event_email or '' }}\n"
        "Représentant : {{ representative_name or '' }}\n\n"
        "Merci de confirmer votre rendez-vous via le bouton."
    )


@emails_bp.route("/send", methods=["GET", "POST"])

@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "Emails personnalises + logos")
def send_bulk():
    form = EmailSendForm()
    orientation_form = OrientationInviteForm()
    _populate_filter_choices(form)
    _populate_filter_choices(orientation_form)
    if form.validate_on_submit():
        template = EmailTemplate.query.order_by(EmailTemplate.id.desc()).first()
        if not template:
            flash("Aucun template email disponible. Cree d'abord un template dans Emails > Templates.", "warning")
            return redirect(url_for("emails.template_list"))
        recipients = resolve_recipients(
            target=form.target.data,
            branch_id=form.branch_id.data or 0,
        )
        if not recipients:
            total_students, students_with_email = _recipient_debug_counts(
                branch_id=form.branch_id.data or 0,
                abroad_only=form.target.data == "abroad_students",
                exclude_abroad=form.target.data != "abroad_students",
            )
            flash(
                f"Aucun destinataire trouve. Etudiants trouves: {total_students}, avec email: {students_with_email}. Verifie cible/filtres.",
                "warning",
            )
            return redirect(url_for("emails.send_bulk"))
        sent_count, total, err = _send_to_recipients(
            recipients=recipients,
            subject_tpl=template.subject,
            body_html_tpl=template.body_html,
            body_text_tpl=template.body_text or "",
            logo_choice="innov",
            logo_display="single",
            cta_label=form.cta_label.data or "",
            template_id=template.id,
        )
        if err:
            flash(err, "danger")
            return redirect(url_for("emails.smtp_settings"))

        add_audit_log(current_user.id, "email_send", f"Envois template: {sent_count}/{total}", branch_id=current_user.branch_id, action="email_send")
        flash(f"Traitement termine: {sent_count}/{total} envoyes.", "success")
        return redirect(url_for("emails.dispatch_history"))

    return render_template("emails/send.html", form=form, orientation_form=orientation_form)


@emails_bp.route("/direct", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def send_direct():
    form = DirectEmailForm()
    _populate_filter_choices(form)

    if form.validate_on_submit():
        recipients = resolve_recipients(
            target=form.target.data,
            branch_id=form.branch_id.data or 0,
        )
        if not recipients:
            total_students, students_with_email = _recipient_debug_counts(
                branch_id=form.branch_id.data or 0,
                abroad_only=form.target.data == "abroad_students",
                exclude_abroad=form.target.data != "abroad_students",
            )
            flash(
                f"Aucun destinataire trouve. Etudiants trouves: {total_students}, avec email: {students_with_email}. Verifie cible/filtres.",
                "warning",
            )
            return redirect(url_for("emails.send_direct"))

        raw_message = (form.body_html.data or "").strip()
        body_html = raw_message
        if "<" not in raw_message and ">" not in raw_message:
            paragraphs = [p.strip() for p in raw_message.replace("\r", "").split("\n\n") if p.strip()]
            body_html = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs) or "<p></p>"

        sent_count, total, err = _send_to_recipients(
            recipients=recipients,
            subject_tpl=form.subject.data,
            body_html_tpl=body_html,
            body_text_tpl=form.body_text.data or "",
            logo_choice="innov",
            logo_display="single",
            cta_label=form.cta_label.data or "",
        )
        if err:
            flash(err, "danger")
            return redirect(url_for("emails.smtp_settings"))

        add_audit_log(current_user.id, "email_direct_send", f"Email direct: {sent_count}/{total}", branch_id=current_user.branch_id, action="email_direct_send")
        flash(f"Email direct traite: {sent_count}/{total}.", "success")
        return redirect(url_for("emails.dispatch_history"))

    return render_template("emails/direct.html", form=form)


@emails_bp.route("/orientation-invite", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
@plan_required("pro", "Emails personnalises + logos")
def send_orientation_invite():
    form = OrientationInviteForm()
    _populate_filter_choices(form)
    if not form.validate_on_submit():
        flash("Formulaire orientation invalide. Verifie les champs obligatoires.", "danger")
        return redirect(url_for("emails.send_bulk"))

    recipients = resolve_recipients(
        target=form.target.data,
        branch_id=form.branch_id.data or 0,
    )
    if not recipients:
        flash("Aucun destinataire trouve pour l'invitation orientation.", "warning")
        return redirect(url_for("emails.send_bulk"))

    cta_label = (form.cta_label.data or "").strip()
    if cta_label:
        missing_branches = _missing_rdv_event_branches(recipients)
        if missing_branches:
            flash(
                "Bouton RDV indisponible: aucun evenement actif pour " + ", ".join(missing_branches) + ". Cree d'abord un evenement dans RDV.",
                "danger",
            )
            return redirect(url_for("emails.send_bulk"))

    sent_count, total, err = _send_to_recipients(
        recipients=recipients,
        subject_tpl=form.subject.data,
        body_html_tpl=_orientation_template_html(),
        body_text_tpl=_orientation_template_text(),
        logo_choice="innov",
        logo_display="single",
        cta_label=cta_label,
        context_overrides={
            "intro_message": (form.intro_message.data or "").strip(),
            "event_title": (form.event_title.data or "").strip(),
            "event_date": (form.event_date.data or "").strip(),
            "event_address": (form.event_address.data or "").strip(),
            "event_phone": (form.event_phone.data or "").strip(),
            "event_email": (form.event_email.data or "").strip(),
            "representative_name": (form.representative_name.data or "").strip(),
        },
    )
    if err:
        flash(err, "danger")
        return redirect(url_for("emails.smtp_settings"))

    add_audit_log(current_user.id, "orientation_invite_send", f"Invitations orientation: {sent_count}/{total}", branch_id=current_user.branch_id, action="orientation_invite_send")
    flash(f"Invitations envoyees: {sent_count}/{total}", "success")
    return redirect(url_for("emails.dispatch_history"))


@emails_bp.route("/history")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def dispatch_history():
    rows = scope_query_by_branch(EmailDispatch.query, EmailDispatch).order_by(EmailDispatch.created_at.desc()).limit(200).all()
    logs = scope_query_by_branch(EmailLog.query, EmailLog).order_by(EmailLog.created_at.desc()).limit(200).all()
    return render_template("emails/history.html", rows=rows, logs=logs)


@emails_bp.route("/history/clear", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def clear_dispatch_history():
    dispatch_q = scope_query_by_branch(EmailDispatch.query, EmailDispatch)
    logs_q = scope_query_by_branch(EmailLog.query, EmailLog)

    deleted_dispatch = dispatch_q.delete(synchronize_session=False)
    deleted_logs = logs_q.delete(synchronize_session=False)
    db.session.commit()

    add_audit_log(
        current_user.id,
        "email_history_clear",
        f"Historique email vide: dispatch={deleted_dispatch}, logs={deleted_logs}",
        branch_id=current_user.branch_id,
        action="email_history_clear",
    )
    flash(f"Historique vide. Dispatch supprimes: {deleted_dispatch}, logs SMTP supprimes: {deleted_logs}.", "success")
    return redirect(url_for("emails.dispatch_history"))








