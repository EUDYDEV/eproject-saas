from app.extensions import db
from app.models import CommissionRecord, CommissionRule, StudyCase


def _pick_matching_rule(case_row):
    if not case_row or not case_row.entity_id:
        return None

    case_status = (case_row.status or "").strip()
    if not case_status:
        return None

    # Priorite a une regle specifique a l'ecole, sinon regle generique de l'entite.
    specific = (
        CommissionRule.query.filter(
            CommissionRule.entity_id == case_row.entity_id,
            CommissionRule.school_id == case_row.school_id,
            CommissionRule.trigger_status == case_status,
        )
        .order_by(CommissionRule.created_at.desc())
        .first()
    )
    if specific:
        return specific

    generic = (
        CommissionRule.query.filter(
            CommissionRule.entity_id == case_row.entity_id,
            CommissionRule.school_id.is_(None),
            CommissionRule.trigger_status == case_status,
        )
        .order_by(CommissionRule.created_at.desc())
        .first()
    )
    return generic


def sync_commission_for_case(case_row):
    """
    Cree/met a jour le record commission pour un dossier si une regle correspond.
    Retourne True si une modification en base est appliquee, sinon False.
    """
    if case_row is None:
        return False

    rule = _pick_matching_rule(case_row)
    if not rule:
        return False

    record = CommissionRecord.query.filter_by(case_id=case_row.id).order_by(CommissionRecord.id.desc()).first()
    if record is None:
        db.session.add(CommissionRecord(case_id=case_row.id, amount=rule.amount_per_student, status="pending"))
        return True

    if record.status == "pending" and float(record.amount or 0) != float(rule.amount_per_student or 0):
        record.amount = rule.amount_per_student
        return True

    return False


def sync_commissions_for_cases(cases):
    changed = False
    for case_row in cases or []:
        if sync_commission_for_case(case_row):
            changed = True
    return changed
