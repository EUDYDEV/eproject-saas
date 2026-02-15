from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import AuditLog, Branch, User
from app.utils.audit import add_audit_log
from app.utils.authz import is_super_admin_platform, role_required, user_branch_ids


logs_bp = Blueprint("logs", __name__, url_prefix="/logs")


@logs_bp.route("/")
@login_required
@role_required("INFORMATICIEN")
def logs_index():
    q = request.args.get("q", "").strip()
    event_type = (request.args.get("event_type") or "").strip()
    selected_branch_id = request.args.get("branch_id", type=int) or 0
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 30

    query = AuditLog.query.join(User, AuditLog.user_id == User.id).outerjoin(Branch, AuditLog.branch_id == Branch.id)

    if is_super_admin_platform(current_user):
        branch_filter_options = Branch.query.order_by(Branch.name.asc()).all()
    else:
        scoped_ids = user_branch_ids(current_user)
        branch_filter_options = Branch.query.filter(Branch.id.in_(scoped_ids)).order_by(Branch.name.asc()).all() if scoped_ids else []
    allowed_branch_ids = {b.id for b in branch_filter_options}

    if selected_branch_id and (is_super_admin_platform(current_user) or selected_branch_id in allowed_branch_ids):
        query = query.filter(AuditLog.branch_id == selected_branch_id)
    else:
        selected_branch_id = 0

    if q:
        like_q = f"%{q}%"
        query = query.filter(
            (AuditLog.type_event.ilike(like_q))
            | (AuditLog.action.ilike(like_q))
            | (AuditLog.details.ilike(like_q))
            | (User.username.ilike(like_q))
            | (User.email.ilike(like_q))
            | (Branch.name.ilike(like_q))
        )

    if event_type:
        query = query.filter(AuditLog.type_event == event_type)

    pagination = query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    rows = pagination.items

    event_options = [
        r[0]
        for r in db.session.query(AuditLog.type_event)
        .distinct()
        .order_by(AuditLog.type_event.asc())
        .all()
        if r[0]
    ]

    return render_template(
        "logs/index.html",
        rows=rows,
        q=q,
        event_type=event_type,
        event_options=event_options,
        pagination=pagination,
        branch_filter=selected_branch_id,
        branch_filter_options=branch_filter_options,
    )


@logs_bp.route("/clear", methods=["POST"])
@login_required
@role_required("INFORMATICIEN")
def clear_logs():
    deleted = AuditLog.query.delete(synchronize_session=False)
    db.session.commit()

    add_audit_log(
        current_user.id,
        "logs_clear",
        f"Historique logs vide: {deleted} lignes supprimees",
        branch_id=current_user.branch_id,
        action="logs_clear",
    )
    flash(f"Logs vides. Lignes supprimees: {deleted}.", "success")
    return redirect(url_for("logs.logs_index"))


