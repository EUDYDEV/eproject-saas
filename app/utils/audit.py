from app.extensions import db
from app.models import AuditLog


def add_audit_log(user_id, type_event, details=None, student_id=None, branch_id=None, action=None):
    row = AuditLog(
        user_id=user_id,
        type_event=type_event,
        details=details,
        student_id=student_id,
        branch_id=branch_id,
        action=action,
    )
    db.session.add(row)
    db.session.commit()
