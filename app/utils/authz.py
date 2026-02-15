from functools import wraps

from flask import abort, session
from flask_login import current_user

from app.models import AgencySubscription, Membership


ROLE_ALIASES = {
    "ADMIN": "ADMIN_BRANCH",
    "INFORMATICIEN": "IT",
    "SECRETAIRE": "EMPLOYEE",
}

PLATFORM_SUPER_ADMIN = "SUPER_ADMIN_PLATFORM"


def normalized_role(role):
    return ROLE_ALIASES.get(role, role)


def normalized_platform_role(role):
    return (role or "").strip().upper()


def is_super_admin_platform(user=None):
    user = user or current_user
    return normalized_platform_role(getattr(user, "platform_role", None)) == PLATFORM_SUPER_ADMIN


def is_founder(user=None):
    user = user or current_user
    return normalized_role(getattr(user, "role", None)) == "FOUNDER"


def is_it(user=None):
    user = user or current_user
    return normalized_role(getattr(user, "role", None)) == "IT"


def is_branch_admin(user=None):
    user = user or current_user
    return normalized_role(getattr(user, "role", None)) == "ADMIN_BRANCH"


def _enterprise_branch_ids_for_user(user):
    """Return all branch IDs belonging to the same owner enterprise as user.branch_id."""
    branch_id = getattr(user, "branch_id", None)
    if not branch_id:
        return set()

    owner_sub = AgencySubscription.query.filter_by(branch_id=branch_id).first()
    if not owner_sub or not owner_sub.owner_user_id:
        return set()

    ids = {
        row.branch_id
        for row in AgencySubscription.query.with_entities(AgencySubscription.branch_id)
        .filter(AgencySubscription.owner_user_id == owner_sub.owner_user_id)
        .all()
        if row.branch_id is not None
    }

    # Fallback: owner memberships can include enterprise branches not yet mirrored in subscriptions.
    owner_memberships = Membership.query.filter_by(user_id=owner_sub.owner_user_id).all()
    ids |= {m.branch_id for m in owner_memberships if m.branch_id is not None}
    return ids


def _user_branch_ids(user=None):
    user = user or current_user
    if not getattr(user, "is_authenticated", False):
        return set()

    rows = Membership.query.filter_by(user_id=user.id).all()
    ids = {r.branch_id for r in rows if r.branch_id is not None}

    # Compat transition: keep legacy branch link while memberships are backfilled.
    if getattr(user, "branch_id", None):
        ids.add(user.branch_id)

    # Business rule: branch staff can see enterprise-wide data.
    role = normalized_role(getattr(user, "role", None))
    if role in ("ADMIN_BRANCH", "EMPLOYEE"):
        ids |= _enterprise_branch_ids_for_user(user)

    return ids


def user_branch_ids(user=None):
    return sorted(_user_branch_ids(user))


def can_access_branch(branch_id, user=None):
    user = user or current_user
    if not getattr(user, "is_authenticated", False):
        return False
    if branch_id is None:
        return False
    if is_super_admin_platform(user):
        return True
    return branch_id in _user_branch_ids(user)


def scope_query_by_branch(query, model_cls):
    if not getattr(current_user, "is_authenticated", False):
        return query

    # Global soft-delete guard when the model supports deleted_at.
    if hasattr(model_cls, "deleted_at"):
        query = query.filter(model_cls.deleted_at.is_(None))

    if not hasattr(model_cls, "branch_id"):
        return query

    if is_super_admin_platform():
        scoped_branch_id = session.get("it_scope_branch_id")
        if scoped_branch_id:
            return query.filter(model_cls.branch_id == scoped_branch_id)
        return query

    allowed_branch_ids = user_branch_ids()
    if not allowed_branch_ids:
        return query.filter(False)
    return query.filter(model_cls.branch_id.in_(allowed_branch_ids))


def role_required(*roles):
    allowed = {normalized_role(r) for r in roles}

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if is_super_admin_platform():
                return view_func(*args, **kwargs)

            current = normalized_role(current_user.role)

            if current == "FOUNDER":
                return view_func(*args, **kwargs)

            if current == "ADMIN_BRANCH" and "EMPLOYEE" in allowed:
                return view_func(*args, **kwargs)

            if current not in allowed:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def branch_access_required(branch_id_getter):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            branch_id = branch_id_getter(*args, **kwargs)
            if not can_access_branch(branch_id):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
