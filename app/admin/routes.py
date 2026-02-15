from datetime import datetime, timedelta
import os
from argon2 import PasswordHasher
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, or_
from sqlalchemy.exc import IntegrityError
import secrets
from types import SimpleNamespace

from app.admin.forms import BranchForm, ITClientEmailForm, PlatformSettingsForm, ResetStudentPasswordForm, ResetUserPasswordForm, UserEmailForm, UserForm
from app.extensions import db
from app.models import AgencySubscription, Branch, EmailLog, Membership, PortalSetting, SMTPSetting, Student, StudentAuth, StudyCase, User
from app.utils.audit import add_audit_log
from app.utils.authz import is_branch_admin, is_founder, is_super_admin_platform, normalized_role, role_required, user_branch_ids
from app.utils.emailer import send_email_smtp
from app.utils.files import save_uploaded_file
from app.utils.subscriptions import get_or_create_portal_settings, is_billable_subscription, plan_required, send_subscription_transactional_email


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
password_hasher = PasswordHasher()


@admin_bp.route("/branches")
@login_required
@role_required("FOUNDER")
@plan_required("enterprise", "Multi-pays complet")
def branches_list():
    membership_rows = Membership.query.filter_by(user_id=current_user.id).all()
    branch_ids = sorted({m.branch_id for m in membership_rows if m.branch_id is not None})

    # Compat transition: keep legacy branch link visible if membership missing.
    if current_user.branch_id:
        branch_ids = sorted(set(branch_ids) | {current_user.branch_id})

    q = (request.args.get("q") or "").strip()

    branches_query = Branch.query.filter(Branch.id.in_(branch_ids)) if branch_ids else Branch.query.filter(False)
    if q:
        like_q = f"%{q}%"
        branches_query = branches_query.filter(
            or_(
                Branch.name.ilike(like_q),
                Branch.country_code.ilike(like_q),
                Branch.city.ilike(like_q),
            )
        )

    branches = branches_query.order_by(Branch.name.asc()).all()
    protected_branch_ids = {b.id for b in branches if b.id == current_user.branch_id}
    subscription_branch_ids = {
        row.branch_id
        for row in AgencySubscription.query.with_entities(AgencySubscription.branch_id).all()
        if row.branch_id is not None
    }
    protected_branch_ids |= subscription_branch_ids
    return render_template(
        "admin/branches_list.html",
        branches=branches,
        protected_branch_ids=protected_branch_ids,
        q=q,
    )


@admin_bp.route("/branches/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER")
@plan_required("enterprise", "Multi-pays complet")
def branches_new():
    form = BranchForm()
    if form.validate_on_submit():
        logo_url = None
        if form.logo_file.data:
            upload_dir = os.path.join(current_app.root_path, "static", "uploads", "agency_logos")
            saved = save_uploaded_file(form.logo_file.data, upload_dir, {"png", "jpg", "jpeg", "webp"})
            if saved:
                logo_url = url_for("static", filename=f"uploads/agency_logos/{saved}")

        row = Branch(
            name=form.name.data.strip(),
            country_code=form.country_code.data.strip().upper(),
            city=(form.city.data or "").strip() or None,
            address=(form.address.data or "").strip() or None,
            phone=(form.phone.data or "").strip() or None,
            email=(form.email.data or "").strip().lower() or None,
            website_url=(form.website_url.data or "").strip() or None,
            timezone=(form.timezone.data or "").strip() or None,
            logo_url=logo_url,
        )
        db.session.add(row)
        db.session.flush()

        existing_membership = Membership.query.filter_by(user_id=current_user.id, branch_id=row.id).first()
        if existing_membership is None:
            db.session.add(Membership(user_id=current_user.id, branch_id=row.id, role="OWNER"))

        # Si le propriétaire a deja un abonnement actif, la nouvelle branche herite d'un abonnement actif.
        owner_active_sub = (
            AgencySubscription.query.filter_by(owner_user_id=current_user.id, status="active")
            .order_by(AgencySubscription.id.asc())
            .first()
        )
        if owner_active_sub and AgencySubscription.query.filter_by(branch_id=row.id).first() is None:
            db.session.add(
                AgencySubscription(
                    branch_id=row.id,
                    owner_user_id=current_user.id,
                    plan_code=owner_active_sub.plan_code or "starter",
                    amount=owner_active_sub.amount or 0.0,
                    currency=owner_active_sub.currency or "XOF",
                    status="active",
                    starts_at=owner_active_sub.starts_at,
                    ends_at=owner_active_sub.ends_at,
                    paid_at=owner_active_sub.paid_at,
                    payment_reference=owner_active_sub.payment_reference,
                )
            )

        if current_user.branch_id is None:
            current_user.branch_id = row.id

        db.session.commit()
        add_audit_log(current_user.id, "branch_create", f"Branche {row.name} crééee", branch_id=row.id, action="branch_create")
        flash("Branche crééee.", "success")
        return redirect(url_for("admin.branches_list"))
    return render_template("admin/branch_form.html", form=form, mode="create")


@admin_bp.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER")
@plan_required("enterprise", "Multi-pays complet")
def branches_edit(branch_id):
    has_membership = Membership.query.filter_by(user_id=current_user.id, branch_id=branch_id).first() is not None
    if not has_membership and current_user.branch_id != branch_id:
        abort(403)
    row = Branch.query.get_or_404(branch_id)
    form = BranchForm(obj=row)
    if form.validate_on_submit():
        if form.logo_file.data:
            upload_dir = os.path.join(current_app.root_path, "static", "uploads", "agency_logos")
            saved = save_uploaded_file(form.logo_file.data, upload_dir, {"png", "jpg", "jpeg", "webp"})
            if saved:
                row.logo_url = url_for("static", filename=f"uploads/agency_logos/{saved}")

        row.name = form.name.data.strip()
        row.country_code = form.country_code.data.strip().upper()
        row.city = (form.city.data or "").strip() or None
        row.address = (form.address.data or "").strip() or None
        row.phone = (form.phone.data or "").strip() or None
        row.email = (form.email.data or "").strip().lower() or None
        row.website_url = (form.website_url.data or "").strip() or None
        row.timezone = (form.timezone.data or "").strip() or None
        db.session.commit()
        add_audit_log(current_user.id, "branch_update", f"Branche {row.name} modifiée", branch_id=row.id, action="branch_update")
        flash("Branche modifiée.", "success")
        return redirect(url_for("admin.branches_list"))
    return render_template("admin/branch_form.html", form=form, mode="edit")


@admin_bp.route("/branches/<int:branch_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER")
@plan_required("enterprise", "Multi-pays complet")
def branches_delete(branch_id):
    has_membership = Membership.query.filter_by(user_id=current_user.id, branch_id=branch_id).first() is not None
    if not has_membership and current_user.branch_id != branch_id:
        abort(403)

    row = Branch.query.get_or_404(branch_id)

    if current_user.branch_id == branch_id:
        flash("Impossible de supprimér la branche principale de votre agence.", "warning")
        return redirect(url_for("admin.branches_list"))

    if AgencySubscription.query.filter_by(branch_id=branch_id).first() is not None:
        flash("Impossible de supprimér cette branche: abonnement agence actif lie a cette branche.", "warning")
        return redirect(url_for("admin.branches_list"))

    if User.query.filter_by(branch_id=branch_id).first() is not None:
        flash("Impossible de supprimér cette branche: des utilisateurs y sont rattaches.", "warning")
        return redirect(url_for("admin.branches_list"))

    if Student.query.filter_by(branch_id=branch_id).first() is not None:
        flash("Impossible de supprimér cette branche: des étudiants y sont rattaches.", "warning")
        return redirect(url_for("admin.branches_list"))

    if StudyCase.query.filter_by(branch_id=branch_id).first() is not None:
        flash("Impossible de supprimér cette branche: des dossiers y sont rattaches.", "warning")
        return redirect(url_for("admin.branches_list"))

    Membership.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)
    db.session.delete(row)
    db.session.commit()
    add_audit_log(current_user.id, "branch_delete", f"Branche {row.name} supprimée", branch_id=branch_id, action="branch_delete")
    flash("Branche supprimée.", "success")
    return redirect(url_for("admin.branches_list"))


@admin_bp.route("/users")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def users_list():
    query = User.query
    role = normalized_role(current_user.role)
    q = (request.args.get("q") or "").strip()
    selected_branch_id = request.args.get("branch_id", type=int) or 0

    if is_branch_admin():
        scoped_ids = user_branch_ids(current_user)
        if scoped_ids:
            query = query.filter(User.branch_id.in_(scoped_ids))
        else:
            query = query.filter(False)
    elif role == "FOUNDER":
        founder_scope_ids = _branch_scope_ids_for_user(current_user)
        if founder_scope_ids:
            query = query.filter(User.branch_id.in_(founder_scope_ids))
        else:
            query = query.filter(False)
    elif not is_super_admin_platform(current_user):
        # Les comptes IT restent prives: visibles uniquement par les IT eux-memes.
        query = query.filter(User.role != "IT")

    if is_super_admin_platform(current_user):
        branch_filter_options = Branch.query.order_by(Branch.name.asc()).all()
    elif role == "FOUNDER":
        founder_scope_ids = _branch_scope_ids_for_user(current_user)
        branch_filter_options = Branch.query.filter(Branch.id.in_(founder_scope_ids)).order_by(Branch.name.asc()).all() if founder_scope_ids else []
    else:
        scoped_ids = user_branch_ids(current_user)
        branch_filter_options = Branch.query.filter(Branch.id.in_(scoped_ids)).order_by(Branch.name.asc()).all() if scoped_ids else []

    allowed_branch_ids = {b.id for b in branch_filter_options}
    if selected_branch_id and (is_super_admin_platform(current_user) or selected_branch_id in allowed_branch_ids):
        query = query.filter(User.branch_id == selected_branch_id)
    else:
        selected_branch_id = 0

    if q:
        like_q = f"%{q}%"
        query = query.filter(
            or_(
                User.username.ilike(like_q),
                User.email.ilike(like_q),
                User.display_name.ilike(like_q),
            )
        )

    role_rank = case(
        (User.role == "FOUNDER", 0),
        (User.role == "IT", 1),
        (User.role == "ADMIN_BRANCH", 2),
        else_=3,
    )
    users = query.order_by(role_rank.asc(), User.username.asc(), User.id.asc()).all()
    manageable_user_ids = {u.id for u in users if _can_manage_user(u)}
    owner_user_ids = {row.owner_user_id for row in AgencySubscription.query.with_entities(AgencySubscription.owner_user_id).all()}
    return render_template(
        "admin/users_list.html",
        users=users,
        manageable_user_ids=manageable_user_ids,
        owner_user_ids=owner_user_ids,
        q=q,
        branch_filter=selected_branch_id,
        branch_filter_options=branch_filter_options,
    )

def _branch_scope_ids_for_user(user=None):
    user = user or current_user
    rows = Membership.query.filter_by(user_id=user.id).all()
    ids = {r.branch_id for r in rows if r.branch_id is not None}
    if getattr(user, "branch_id", None):
        ids.add(user.branch_id)
    return sorted(ids)


def _admin_local_branch_ids(user=None):
    user = user or current_user
    # ADMIN_BRANCH: localite stricte (une seule branche)
    if is_branch_admin(user):
        return [user.branch_id] if user.branch_id else []
    return user_branch_ids(user)


def _can_manage_user(target_user):
    if is_branch_admin():
        local_ids = set(_admin_local_branch_ids(current_user))
        if target_user.branch_id not in local_ids:
            return False
        return normalized_role(target_user.role) == "EMPLOYEE"

    if normalized_role(current_user.role) == "FOUNDER":
        founder_scope_ids = set(_branch_scope_ids_for_user(current_user))
        if target_user.branch_id not in founder_scope_ids:
            return False
        return normalized_role(target_user.role) in ("ADMIN_BRANCH", "EMPLOYEE", "ADMIN")

    if is_super_admin_platform(current_user):
        return True

    return False


def _effective_smtp_settings():
    role = normalized_role(current_user.role)
    if role == "ADMIN_BRANCH" and current_user.branch_id:
        settings = SMTPSetting.query.filter_by(branch_id=current_user.branch_id).first()
        if settings:
            return settings

    settings = SMTPSetting.query.filter_by(branch_id=None).first()
    if settings:
        return settings

    from flask import current_app
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


def _prepare_user_form(form, mode="create"):
    branch_choices = [(0, "-- Sans branche --")] + [(b.id, f"{b.name} ({b.country_code})") for b in Branch.query.order_by(Branch.name.asc()).all()]
    form.branch_id.choices = branch_choices

    if is_branch_admin():
        form.role.choices = [("EMPLOYEE", "EMPLOYEE")]
        branch = Branch.query.get(current_user.branch_id) if current_user.branch_id else None
        if branch:
            form.branch_id.choices = [(branch.id, f"{branch.name} ({branch.country_code})")]
            form.branch_id.data = branch.id
        elif current_user.branch_id:
            form.branch_id.choices = [(current_user.branch_id, f"Branche #{current_user.branch_id}")]
            form.branch_id.data = current_user.branch_id
        else:
            form.branch_id.choices = []
    elif is_super_admin_platform(current_user):
        form.role.choices = [
            ("FOUNDER", "FOUNDER"),
            ("IT", "IT"),
            ("ADMIN_BRANCH", "ADMIN_BRANCH"),
            ("EMPLOYEE", "EMPLOYEE"),
        ]
    elif normalized_role(current_user.role) == "FOUNDER":
        form.role.choices = [
            ("ADMIN_BRANCH", "ADMIN_BRANCH"),
            ("EMPLOYEE", "EMPLOYEE"),
        ]
        founder_scope_ids = _branch_scope_ids_for_user(current_user)
        if founder_scope_ids:
            scoped_branches = Branch.query.filter(Branch.id.in_(founder_scope_ids)).order_by(Branch.name.asc()).all()
            form.branch_id.choices = [(b.id, f"{b.name} ({b.country_code})") for b in scoped_branches]
            if mode == "create":
                scoped_ids = [b.id for b in scoped_branches]
                if current_user.branch_id in scoped_ids:
                    form.branch_id.data = current_user.branch_id
                else:
                    form.branch_id.data = scoped_branches[0].id if scoped_branches else 0
    else:
        form.role.choices = [
            ("ADMIN_BRANCH", "ADMIN_BRANCH"),
            ("EMPLOYEE", "EMPLOYEE"),
        ]

    if mode == "create" and is_branch_admin():
        local_choice_ids = [bid for bid, _ in form.branch_id.choices]
        if current_user.branch_id in local_choice_ids:
            form.branch_id.data = current_user.branch_id


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def users_new():
    form = UserForm()
    _prepare_user_form(form, mode="create")

    if form.validate_on_submit():
        if normalized_role(current_user.role) == "FOUNDER" and form.role.data in ("FOUNDER", "IT"):
            flash("Seul le role IT peut créer un compte FOUNDER ou IT.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")
        if not form.password.data:
            flash("Mot de passe requis pour créer l'utilisateur.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        branch_id = form.branch_id.data if form.branch_id.data != 0 else None
        if is_branch_admin() and current_user.branch_id:
            # Verrouille la creation employe sur la localite du responsable.
            branch_id = current_user.branch_id
        if form.role.data in ("IT", "FOUNDER"):
            branch_id = None

        # Safety: branch-scoped roles must always be attached to a concrete branch.
        if form.role.data in ("ADMIN_BRANCH", "EMPLOYEE", "ADMIN") and not branch_id:
            if normalized_role(current_user.role) == "FOUNDER":
                scope_ids = _branch_scope_ids_for_user(current_user)
                if current_user.branch_id and current_user.branch_id in scope_ids:
                    branch_id = current_user.branch_id
                elif scope_ids:
                    branch_id = scope_ids[0]
            elif is_branch_admin() and current_user.branch_id:
                branch_id = current_user.branch_id

        if is_branch_admin():
            local_ids = set(_admin_local_branch_ids(current_user))
            if not branch_id or branch_id not in local_ids:
                flash("Selectionne une branche de ta localite.", "danger")
                return render_template("admin/user_form.html", form=form, mode="create")

        if normalized_role(current_user.role) == "FOUNDER":
            allowed = set(_branch_scope_ids_for_user(current_user))
            if form.role.data in ("ADMIN_BRANCH", "EMPLOYEE", "ADMIN") and (not branch_id or branch_id not in allowed):
                flash("Selectionne une branche autorisee pour cet utilisateur.", "danger")
                return render_template("admin/user_form.html", form=form, mode="create")

        username_clean = form.username.data.strip()
        email_clean = form.email.data.strip().lower()

        if User.query.filter(User.email == email_clean).first() is not None:
            flash("Cet email existe deja. Utilise un autre email.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        if User.query.filter(User.username == username_clean).first() is not None:
            flash("Ce username existe deja. Utilise un autre username.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        user = User(
            username=username_clean,
            email=email_clean,
            role=form.role.data,
            branch_id=branch_id,
            password_hash=password_hasher.hash(form.password.data),
            is_active=form.is_active.data,
            must_change_password=form.must_change_password.data,
        )

        try:
            db.session.add(user)
            db.session.flush()
            if branch_id:
                existing_membership = Membership.query.filter_by(user_id=user.id, branch_id=branch_id).first()
                if existing_membership is None:
                    membership_role = "OWNER" if form.role.data == "FOUNDER" else "STAFF"
                    db.session.add(Membership(user_id=user.id, branch_id=branch_id, role=membership_role))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Impossible de créer cet utilisateur: email ou username deja utilise.", "danger")
            return render_template("admin/user_form.html", form=form, mode="create")

        add_audit_log(current_user.id, "user_create", f"Utilisateur {user.username} créée", branch_id=user.branch_id, action="user_create")
        flash("Utilisateur créée.", "success")
        return redirect(url_for("admin.users_list"))
    return render_template("admin/user_form.html", form=form, mode="create")
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def users_edit(user_id):
    user = User.query.get_or_404(user_id)

    if is_branch_admin():
        if user.branch_id not in set(_admin_local_branch_ids(current_user)):
            abort(403)
    if normalized_role(current_user.role) == "FOUNDER":
        if normalized_role(user.role) in ("FOUNDER", "IT"):
            abort(403)
        if user.branch_id not in set(_branch_scope_ids_for_user(current_user)):
            abort(403)

    form = UserForm(obj=user)
    _prepare_user_form(form, mode="edit")

    if form.validate_on_submit():
        if normalized_role(current_user.role) == "FOUNDER" and form.role.data in ("FOUNDER", "IT"):
            flash("Seul le role IT peut modifiér un compte FOUNDER ou IT.", "danger")
            return render_template("admin/user_form.html", form=form, mode="edit")
        old_branch_id = user.branch_id
        user.username = form.username.data.strip()
        user.email = form.email.data.strip().lower()
        user.role = form.role.data

        if is_branch_admin():
            selected_branch_id = form.branch_id.data if form.branch_id.data != 0 else None
            local_ids = set(_admin_local_branch_ids(current_user))
            if not selected_branch_id or selected_branch_id not in local_ids:
                flash("Selectionne une branche de ta localite.", "danger")
                return render_template("admin/user_form.html", form=form, mode="edit")
            user.branch_id = selected_branch_id
        elif normalized_role(current_user.role) == "FOUNDER":
            selected_branch_id = form.branch_id.data if form.branch_id.data != 0 else None
            allowed = set(_branch_scope_ids_for_user(current_user))
            if not selected_branch_id or selected_branch_id not in allowed:
                flash("Selectionne une branche autorisee pour cet utilisateur.", "danger")
                return render_template("admin/user_form.html", form=form, mode="edit")
            user.branch_id = selected_branch_id
        else:
            user.branch_id = form.branch_id.data if form.branch_id.data != 0 else None
            if user.role in ("IT", "FOUNDER"):
                user.branch_id = None

        user.is_active = form.is_active.data
        user.must_change_password = form.must_change_password.data
        if form.password.data:
            user.password_hash = password_hasher.hash(form.password.data)

        if old_branch_id and old_branch_id != user.branch_id:
            Membership.query.filter_by(user_id=user.id, branch_id=old_branch_id).delete(synchronize_session=False)

        if user.branch_id:
            existing_membership = Membership.query.filter_by(user_id=user.id, branch_id=user.branch_id).first()
            if existing_membership is None:
                membership_role = "OWNER" if user.role == "FOUNDER" else "STAFF"
                db.session.add(Membership(user_id=user.id, branch_id=user.branch_id, role=membership_role))
            elif user.role == "FOUNDER":
                existing_membership.role = "OWNER"

            # Regle metier: un ADMIN_BRANCH/EMPLOYEE travaille sur une seule branche.
            if user.role in ("ADMIN_BRANCH", "EMPLOYEE"):
                Membership.query.filter(
                    Membership.user_id == user.id,
                    Membership.branch_id != user.branch_id,
                ).delete(synchronize_session=False)

        username_clean = user.username.strip()
        email_clean = user.email.strip().lower()

        email_conflict = User.query.filter(User.email == email_clean, User.id != user.id).first()
        if email_conflict is not None:
            flash("Cet email existe deja pour un autre utilisateur.", "danger")
            return render_template("admin/user_form.html", form=form, mode="edit")

        username_conflict = User.query.filter(User.username == username_clean, User.id != user.id).first()
        if username_conflict is not None:
            flash("Ce username existe deja pour un autre utilisateur.", "danger")
            return render_template("admin/user_form.html", form=form, mode="edit")

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Mise à jour impossible: email ou username deja utilise.", "danger")
            return render_template("admin/user_form.html", form=form, mode="edit")

        add_audit_log(current_user.id, "user_update", f"Utilisateur {user.username} modifié", branch_id=user.branch_id, action="user_update")
        flash("Utilisateur modifié.", "success")
        return redirect(url_for("admin.users_list"))
    return render_template("admin/user_form.html", form=form, mode="edit")


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def users_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("Tu ne peux pas supprimér ton propre compte.", "danger")
        return redirect(url_for("admin.users_list"))
    if not _can_manage_user(user):
        abort(403)

    username = user.username
    branch_id = user.branch_id
    Membership.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    add_audit_log(current_user.id, "user_delete", f"Utilisateur {username} supprimé", branch_id=branch_id, action="user_delete")
    flash("Utilisateur supprimé.", "success")
    return redirect(url_for("admin.users_list"))


@admin_bp.route("/users/<int:user_id>/email", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "IT")
def users_send_email(user_id):
    user = User.query.get_or_404(user_id)
    if not _can_manage_user(user) and user.id != current_user.id:
        abort(403)

    form = UserEmailForm()
    if form.validate_on_submit():
        smtp = _effective_smtp_settings()
        if not smtp:
            flash("SMTP non configure. Configure d'abord Emails > SMTP.", "danger")
            return redirect(url_for("emails.smtp_settings"))
        try:
            html_body = "<p>" + (form.body.data or "").replace("\r", "").replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
            send_email_smtp(smtp, user.email, form.subject.data.strip(), html_body, form.body.data or "")
            log = EmailLog(
                branch_id=user.branch_id,
                to_email=user.email,
                subject=form.subject.data.strip(),
                status="sent",
                sent_by=current_user.id,
            )
            db.session.add(log)
            db.session.commit()
            add_audit_log(current_user.id, "user_email_send", f"Email envoyé a {user.username}", branch_id=user.branch_id, action="user_email_send")
            flash("Email envoyé a l'utilisateur.", "success")
            return redirect(url_for("admin.users_list"))
        except Exception as exc:
            db.session.add(
                EmailLog(
                    branch_id=user.branch_id,
                    to_email=user.email,
                    subject=form.subject.data.strip(),
                    status="failed",
                    error=str(exc),
                    sent_by=current_user.id,
                )
            )
            db.session.commit()
            flash(f"Echec envoi email: {exc}", "danger")

    if not form.subject.data:
        form.subject.data = "Message InnovFormation"
    return render_template("admin/user_email_form.html", form=form, user=user)


@admin_bp.route("/it/settings", methods=["GET", "POST"])
@login_required
@role_required("IT")
def it_settings():
    if not is_super_admin_platform(current_user):
        abort(403)
    settings = get_or_create_portal_settings()

    form = PlatformSettingsForm(obj=settings)
    if form.validate_on_submit():
        settings.site_name = (form.site_name.data or "").strip() or "E-PROJECT"
        settings.site_tagline = (form.site_tagline.data or "").strip() or None
        settings.site_footer_text = (form.site_footer_text.data or "").strip() or None

        if form.site_logo_file.data:
            upload_dir = os.path.join(current_app.root_path, "static", "uploads", "platform_logos")
            saved = save_uploaded_file(form.site_logo_file.data, upload_dir, {"png", "jpg", "jpeg", "webp"})
            if saved:
                settings.site_logo_url = url_for("static", filename=f"uploads/platform_logos/{saved}")
        else:
            settings.site_logo_url = (form.site_logo_url.data or "").strip() or settings.site_logo_url or None
        settings.payment_link = (form.payment_link.data or "").strip() or None
        settings.payment_link_starter = (form.payment_link_starter.data or "").strip() or None
        settings.payment_link_pro = (form.payment_link_pro.data or "").strip() or None
        settings.payment_link_enterprise = (form.payment_link_enterprise.data or "").strip() or None
        settings.billing_sender_email = (form.billing_sender_email.data or "").strip().lower() or "eudyproject@gmail.com"
        settings.plan_currency = ((form.plan_currency.data or "").strip() or "XOF").upper()
        settings.expiry_notice_days = _to_int(form.expiry_notice_days.data, default=7)
        settings.plan_starter_price = _to_float(form.plan_starter_price.data, default=0.0)
        settings.plan_pro_price = _to_float(form.plan_pro_price.data, default=0.0)
        settings.plan_enterprise_price = _to_float(form.plan_enterprise_price.data, default=0.0)
        db.session.commit()
        add_audit_log(current_user.id, "platform_settings_update", "Paramètres plateforme modifiés", branch_id=current_user.branch_id, action="platform_settings_update")
        flash("Paramètres plateforme enregistrés.", "success")
        return redirect(url_for("admin.it_settings"))

    if not form.plan_starter_price.data:
        form.plan_starter_price.data = f"{float(settings.plan_starter_price or 0):.0f}"
    if not form.plan_pro_price.data:
        form.plan_pro_price.data = f"{float(settings.plan_pro_price or 0):.0f}"
    if not form.plan_enterprise_price.data:
        form.plan_enterprise_price.data = f"{float(settings.plan_enterprise_price or 0):.0f}"
    if not form.plan_currency.data:
        form.plan_currency.data = (settings.plan_currency or "XOF").upper()
    if not form.expiry_notice_days.data:
        form.expiry_notice_days.data = str(settings.expiry_notice_days or 7)
    if not form.billing_sender_email.data:
        form.billing_sender_email.data = settings.billing_sender_email or "eudyproject@gmail.com"

    return render_template("admin/it_settings.html", form=form)


@admin_bp.route("/it/client-emails", methods=["GET", "POST"])
@login_required
@role_required("IT")
def it_client_emails():
    if not is_super_admin_platform(current_user):
        abort(403)

    form = ITClientEmailForm()

    branch_rows = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
        .order_by(Branch.name.asc())
        .all()
    )
    form.branch_id.choices = [(0, "Toutes les agences")] + [
        (row.branch_id, row.branch.name if row.branch else f"Branche #{row.branch_id}")
        for row in branch_rows
    ]

    if form.validate_on_submit():
        smtp = _effective_smtp_settings()
        if not smtp:
            flash("SMTP IT non configure. Configure d'abord SMTP avant envoi.", "danger")
            return redirect(url_for("emails.smtp_settings"))

        query = AgencySubscription.query.join(User, AgencySubscription.owner_user_id == User.id).filter(AgencySubscription.branch_id == User.branch_id)
        if form.subscription_status.data != "all":
            query = query.filter(AgencySubscription.status == form.subscription_status.data)
        if form.branch_id.data:
            query = query.filter(AgencySubscription.branch_id == form.branch_id.data)

        subscriptions = query.join(Branch, AgencySubscription.branch_id == Branch.id).order_by(Branch.name.asc(), User.username.asc()).all()

        recipients = []
        seen = set()
        for sub in subscriptions:
            owner = sub.owner_user
            email = (owner.email or "").strip().lower() if owner else ""
            if not owner or not owner.is_active or not email:
                continue
            if email in seen:
                continue
            seen.add(email)
            recipients.append((email, sub.branch_id))

        if not recipients:
            flash("Aucun client destinataire pour ce filtre.", "warning")
            return redirect(url_for("admin.it_client_emails", view="send"))

        body_raw = (form.body.data or "").strip()
        html_body = "<p>" + body_raw.replace("\r", "").replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"

        sent = 0
        failed = 0
        for to_email, branch_id in recipients:
            try:
                send_email_smtp(smtp, to_email, form.subject.data.strip(), html_body, body_raw)
                db.session.add(
                    EmailLog(
                        branch_id=branch_id,
                        to_email=to_email,
                        subject=form.subject.data.strip(),
                        status="sent",
                        sent_by=current_user.id,
                    )
                )
                sent += 1
            except Exception as exc:
                db.session.add(
                    EmailLog(
                        branch_id=branch_id,
                        to_email=to_email,
                        subject=form.subject.data.strip(),
                        status="failed",
                        error=str(exc),
                        sent_by=current_user.id,
                    )
                )
                failed += 1

        db.session.commit()
        add_audit_log(
            current_user.id,
            "it_client_email_send",
            f"Email clients SaaS: sent={sent}, failed={failed}, filter={form.subscription_status.data}",
            branch_id=None,
            action="it_client_email_send",
        )

        if failed:
            flash(f"Envoi termine: {sent} envoyés, {failed} en echec.", "warning")
        else:
            flash(f"Envoi termine: {sent} envoyés.", "success")
        return redirect(url_for("admin.it_client_emails", view="send"))

    q = (request.args.get("q") or "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15

    preview_query = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
    )
    if q:
        like_q = f"%{q}%"
        preview_query = preview_query.filter(
            or_(
                Branch.name.ilike(like_q),
                User.username.ilike(like_q),
                User.email.ilike(like_q),
                AgencySubscription.status.ilike(like_q),
                AgencySubscription.plan_code.ilike(like_q),
            )
        )

    pagination = (
        preview_query.order_by(Branch.name.asc(), User.username.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    subscriptions_preview = pagination.items
    return render_template(
        "admin/it_client_emails.html",
        form=form,
        subscriptions=subscriptions_preview,
        pagination=pagination,
        q=q,
    )



@admin_bp.route("/it/clients")
@login_required
@role_required("IT")
def it_clients_list():
    if not is_super_admin_platform(current_user):
        abort(403)

    q = (request.args.get("q") or "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15

    preview_query = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
    )
    if q:
        like_q = f"%{q}%"
        preview_query = preview_query.filter(
            or_(
                Branch.name.ilike(like_q),
                User.username.ilike(like_q),
                User.email.ilike(like_q),
                AgencySubscription.status.ilike(like_q),
                AgencySubscription.plan_code.ilike(like_q),
            )
        )

    pagination = (
        preview_query.order_by(Branch.name.asc(), User.username.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "admin/it_clients_list.html",
        subscriptions=pagination.items,
        pagination=pagination,
        q=q,
    )
@admin_bp.route("/it/client-emails/<int:owner_user_id>/users")
@login_required
@role_required("IT")
def it_client_owner_users(owner_user_id):
    if not is_super_admin_platform(current_user):
        abort(403)

    owner = User.query.get_or_404(owner_user_id)

    owner_branch_ids = {owner.branch_id} if owner.branch_id else set()
    owner_memberships = Membership.query.filter_by(user_id=owner.id).all()
    owner_branch_ids |= {m.branch_id for m in owner_memberships if m.branch_id is not None}

    branch_rows = []
    if owner_branch_ids:
        branch_rows = Branch.query.filter(Branch.id.in_(sorted(owner_branch_ids))).order_by(Branch.name.asc()).all()

    users = []
    if owner_branch_ids:
        users = (
            User.query.filter(User.branch_id.in_(sorted(owner_branch_ids)), User.id != owner.id, User.role != "IT")
            .order_by(User.role.asc(), User.username.asc())
            .all()
        )

    return render_template(
        "admin/it_client_owner_users.html",
        owner=owner,
        branches=branch_rows,
        users=users,
    )


@admin_bp.route("/it/subscriptions")
@login_required
@role_required("IT")
def it_subscriptions():
    if not is_super_admin_platform(current_user):
        abort(403)
    q = (request.args.get("q") or "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 15

    query = (
        AgencySubscription.query.join(Branch, AgencySubscription.branch_id == Branch.id)
        .join(User, AgencySubscription.owner_user_id == User.id)
        .filter(AgencySubscription.branch_id == User.branch_id)
    )
    if q:
        like_q = f"%{q}%"
        query = query.filter(
            or_(
                Branch.name.ilike(like_q),
                User.username.ilike(like_q),
                User.email.ilike(like_q),
                AgencySubscription.status.ilike(like_q),
                AgencySubscription.plan_code.ilike(like_q),
                AgencySubscription.payment_reference.ilike(like_q),
            )
        )

    pagination = (
        query.order_by(AgencySubscription.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    rows = pagination.items
    return render_template("admin/it_subscriptions.html", rows=rows, pagination=pagination, q=q)


@admin_bp.route("/it/subscriptions/<int:subscription_id>/activate", methods=["POST"])
@login_required
@role_required("IT")
def activate_subscription(subscription_id):
    if not is_super_admin_platform(current_user):
        abort(403)
    sub = AgencySubscription.query.get_or_404(subscription_id)
    if not is_billable_subscription(sub):
        flash("Abonnement non facturable: seule l'agence propriétaire est gerable ici.", "warning")
        return redirect(url_for("admin.it_subscriptions"))
    now = datetime.utcnow()
    start = now
    if sub.ends_at and sub.ends_at > now:
        start = sub.ends_at
    sub.status = "active"
    sub.paid_at = now
    sub.starts_at = start
    sub.ends_at = start + timedelta(days=30)
    db.session.commit()

    owner_name = (sub.owner_user.display_name or sub.owner_user.username) if sub.owner_user else "Client"
    end_label = sub.ends_at.strftime("%d/%m/%Y") if sub.ends_at else "-"
    subject = "Abonnement activé - votre compte est désormais actif"
    html_body = (
        f"<p>Bonjour {owner_name},</p>"
        "<p>Bonne nouvelle: votre abonnement a été activé par l'équipe IT E-PROJECT.</p>"
        f"<p><strong>Plan:</strong> {(sub.plan_code or 'starter').upper()}<br>"
        f"<strong>Validité jusqu'au :</strong> {end_label}</p>"
        "<p>Vous pouvez maintenant vous connecter et utiliser toutes les fonctionnalités de votre dashboard.</p>"
        "<p>Cordialement,<br>Service facturation E-PROJECT</p>"
    )
    text_body = (
        f"Bonjour {owner_name},\n\n"
        "Bonne nouvelle: votre abonnement a été activé par l'équipe IT E-PROJECT.\n"
        f"Plan: {(sub.plan_code or 'starter').upper()}\n"
        f"Validité jusqu'au : {end_label}\n\n"
        "Vous pouvez maintenant vous connecter et utiliser toutes les fonctionnalités de votre dashboard.\n\n"
        "Service facturation E-PROJECT"
    )
    send_subscription_transactional_email(sub, subject, html_body, text_body, sent_by=current_user.id)

    add_audit_log(current_user.id, "subscription_activate", f"Abonnement active pour branche #{sub.branch_id}", branch_id=sub.branch_id, action="subscription_activate")
    flash("Abonnement active pour 30 jours.", "success")
    return redirect(url_for("admin.it_subscriptions"))


@admin_bp.route("/it/subscriptions/<int:subscription_id>/expire", methods=["POST"])
@login_required
@role_required("IT")
def expire_subscription(subscription_id):
    if not is_super_admin_platform(current_user):
        abort(403)
    sub = AgencySubscription.query.get_or_404(subscription_id)
    if not is_billable_subscription(sub):
        flash("Abonnement non facturable: seule l'agence propriétaire est gerable ici.", "warning")
        return redirect(url_for("admin.it_subscriptions"))
    sub.status = "expired"
    sub.ends_at = datetime.utcnow()
    db.session.commit()

    owner_name = (sub.owner_user.display_name or sub.owner_user.username) if sub.owner_user else "Client"
    subject = "Abonnement expiré - réabonnement requis"
    html_body = (
        f"<p>Bonjour {owner_name},</p>"
        "<p>Votre abonnement a été marqué expiré par l'équipe IT E-PROJECT.</p>"
        "<p>Pour réactiver votre compte, connectez-vous et effectuez le réabonnement depuis votre espace abonnement.</p>"
        "<p>Cordialement,<br>Service facturation E-PROJECT</p>"
    )
    text_body = (
        f"Bonjour {owner_name},\n\n"
        "Votre abonnement a été marqué expiré par l'équipe IT E-PROJECT.\n"
        "Pour réactiver votre compte, connectez-vous et effectuez le réabonnement depuis votre espace abonnement.\n\n"
        "Service facturation E-PROJECT"
    )
    send_subscription_transactional_email(sub, subject, html_body, text_body, sent_by=current_user.id)

    add_audit_log(current_user.id, "subscription_expire", f"Abonnement expire pour branche #{sub.branch_id}", branch_id=sub.branch_id, action="subscription_expire")
    flash("Abonnement marque expire.", "warning")
    return redirect(url_for("admin.it_subscriptions"))


def _temp_password():
    return secrets.token_urlsafe(9)[:12]


def _to_float(raw_value, default=0.0):
    try:
        return float((raw_value or "").replace(",", ".").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def _to_int(raw_value, default=7):
    try:
        return int((raw_value or "").strip() or default)
    except (TypeError, ValueError):
        return int(default)


@admin_bp.route("/it/password-resets", methods=["GET", "POST"])
@login_required
@role_required("IT")
def it_password_resets():
    if not is_super_admin_platform(current_user):
        abort(403)
    user_form = ResetUserPasswordForm(prefix="user")
    student_form = ResetStudentPasswordForm(prefix="student")

    users = User.query.order_by(User.username.asc()).all()
    user_form.user_id.choices = [(u.id, f"{u.username} ({u.role})") for u in users]

    students = Student.query.order_by(Student.matricule.asc()).all()
    student_form.student_id.choices = [(s.id, f"{s.matricule} - {s.nom} {s.prenoms}") for s in students]

    if user_form.submit_user.data and user_form.validate_on_submit():
        user = User.query.get_or_404(user_form.user_id.data)
        new_password = (user_form.new_password.data or "").strip() or _temp_password()
        user.password_hash = password_hasher.hash(new_password)
        user.must_change_password = bool(user_form.force_change.data)
        db.session.commit()
        add_audit_log(current_user.id, "user_password_reset", f"Password reset utilisateur {user.username}", branch_id=user.branch_id, action="user_password_reset")
        flash(f"Mot de passe utilisateur réinitialisé: {user.username} -> {new_password}", "success")
        return redirect(url_for("admin.it_password_resets"))

    if student_form.submit_student.data and student_form.validate_on_submit():
        student = Student.query.get_or_404(student_form.student_id.data)
        auth = StudentAuth.query.filter_by(student_id=student.id).first()
        if auth is None:
            auth = StudentAuth(student_id=student.id, password_hash=password_hasher.hash(_temp_password()), must_change_password=True)
            db.session.add(auth)
            db.session.flush()
        new_password = (student_form.new_password.data or "").strip() or _temp_password()
        auth.password_hash = password_hasher.hash(new_password)
        auth.must_change_password = bool(student_form.force_change.data)
        db.session.commit()
        add_audit_log(current_user.id, "student_password_reset", f"Password reset étudiant {student.matricule}", student_id=student.id, branch_id=student.branch_id, action="student_password_reset")
        flash(f"Mot de passe étudiant réinitialisé: {student.matricule} -> {new_password}", "success")
        return redirect(url_for("admin.it_password_resets"))

    return render_template("admin/it_password_resets.html", user_form=user_form, student_form=student_form)













