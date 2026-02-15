import os
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import ArrivalSupport, Branch, CasePayment, CaseStage, CommissionRecord, Document, Entity, School, Student, StudyCase
from app.procedures.forms import CaseDocumentForm, CaseDocumentStatusForm, CasePaymentForm, CaseStageForm, EntityForm, SchoolForm, StageStatusForm, StudyCaseForm
from app.utils.audit import add_audit_log
from app.utils.authz import can_access_branch, normalized_role, role_required, scope_query_by_branch, user_branch_ids
from app.utils.commissions import sync_commission_for_case
from app.utils.files import save_uploaded_file
from app.utils.subscriptions import user_plan_allows


procedures_bp = Blueprint("procedures", __name__, url_prefix="/procedures")
@procedures_bp.before_request
def _enforce_pro_plan_for_procedures():
    if not current_user.is_authenticated:
        return None
    if user_plan_allows("pro", current_user):
        return None
    flash("Fonction reservee au plan PRO (Suivi procedures et commissions).", "warning")
    return redirect(url_for("dashboard.index"))



def _enforce_case_access(case_row):
    if not can_access_branch(case_row.branch_id):
        abort(403)


def _sync_case_stages_with_status(case_row):
    stages = CaseStage.query.filter_by(case_id=case_row.id).all()
    if not stages:
        return

    if case_row.status in {"arrive", "installe"}:
        for st in stages:
            st.status = "done"
            if not st.completed_at:
                st.completed_at = datetime.utcnow()
    elif case_row.status == "parti":
        for st in stages:
            if st.name == "Arrivee et installation":
                st.status = "doing"
                st.completed_at = None
            else:
                st.status = "done"
                if not st.completed_at:
                    st.completed_at = datetime.utcnow()


def _eligible_students_query(include_student_id=None):
    query = scope_query_by_branch(Student.query, Student)
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
    if include_student_id:
        query = query.filter(or_(~abroad_exists, Student.id == include_student_id))
    else:
        query = query.filter(~abroad_exists)
    return query


def _student_choices(include_student_id=None):
    rows = _eligible_students_query(include_student_id=include_student_id).order_by(Student.nom.asc(), Student.prenoms.asc()).all()
    return [(s.id, f"{s.matricule} - {s.nom} {s.prenoms}") for s in rows]


def _visible_branch_ids_for_actor():
    role = normalized_role(getattr(current_user, "role", None))
    if role == "IT":
        return [b.id for b in Branch.query.order_by(Branch.name.asc()).all()]
    ids = user_branch_ids(current_user)
    return sorted(set(ids))


def _actor_branch_id():
    if getattr(current_user, "branch_id", None):
        return current_user.branch_id
    ids = _visible_branch_ids_for_actor()
    return ids[0] if ids else None


def _selected_branch_filter(default=0):
    branch_id = request.args.get("branch_id", type=int)
    if branch_id is None:
        branch_id = request.form.get("branch_id", type=int)
    branch_id = branch_id or default
    visible = set(_visible_branch_ids_for_actor())
    if branch_id and branch_id not in visible:
        return 0
    return branch_id


def _entity_query_scoped(selected_branch_id=0):
    visible_ids = _visible_branch_ids_for_actor()
    if not visible_ids:
        return Entity.query.filter(False)

    legacy_scope_exists = (
        db.session.query(StudyCase.id)
        .filter(StudyCase.entity_id == Entity.id, StudyCase.branch_id.in_(visible_ids))
        .exists()
    )

    query = Entity.query.filter(
        or_(
            Entity.branch_id.in_(visible_ids),
            and_(Entity.branch_id.is_(None), legacy_scope_exists),
        )
    )

    if selected_branch_id:
        selected_legacy_exists = (
            db.session.query(StudyCase.id)
            .filter(StudyCase.entity_id == Entity.id, StudyCase.branch_id == selected_branch_id)
            .exists()
        )
        query = query.filter(
            or_(
                Entity.branch_id == selected_branch_id,
                and_(Entity.branch_id.is_(None), selected_legacy_exists),
            )
        )

    return query


def _school_query_scoped(selected_branch_id=0):
    visible_ids = _visible_branch_ids_for_actor()
    if not visible_ids:
        return School.query.filter(False)

    legacy_scope_exists = (
        db.session.query(StudyCase.id)
        .filter(StudyCase.school_id == School.id, StudyCase.branch_id.in_(visible_ids))
        .exists()
    )

    query = School.query.filter(
        or_(
            School.branch_id.in_(visible_ids),
            and_(School.branch_id.is_(None), legacy_scope_exists),
        )
    )

    if selected_branch_id:
        selected_legacy_exists = (
            db.session.query(StudyCase.id)
            .filter(StudyCase.school_id == School.id, StudyCase.branch_id == selected_branch_id)
            .exists()
        )
        query = query.filter(
            or_(
                School.branch_id == selected_branch_id,
                and_(School.branch_id.is_(None), selected_legacy_exists),
            )
        )

    return query


def _enforce_entity_access(entity_row):
    if _entity_query_scoped().filter(Entity.id == entity_row.id).first() is None:
        abort(403)


def _enforce_school_access(school_row):
    if _school_query_scoped().filter(School.id == school_row.id).first() is None:
        abort(403)


def _entity_choices(required=False, branch_id=0):
    rows = _entity_query_scoped(branch_id).order_by(Entity.name.asc()).all()
    choices = [(e.id, e.name) for e in rows]
    if not required:
        return [(0, "Aucune")] + choices
    return choices


def _school_choices(branch_id=0):
    rows = _school_query_scoped(branch_id).order_by(School.name.asc()).all()
    choices = [(s.id, f"{s.name} ({s.country or 'N/A'})") for s in rows]
    return [(0, "Aucune")] + choices


@procedures_bp.route("/students/search")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def search_students():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    rows = (
        _eligible_students_query()
        .filter(
            (Student.matricule.ilike(f"%{q}%"))
            | (Student.nom.ilike(f"%{q}%"))
            | (Student.prenoms.ilike(f"%{q}%"))
        )
        .order_by(Student.nom.asc(), Student.prenoms.asc())
        .limit(20)
        .all()
    )
    return jsonify(
        {
            "results": [
                {
                    "id": s.id,
                    "matricule": s.matricule,
                    "nom": s.nom,
                    "prenoms": s.prenoms,
                    "label": f"{s.matricule} - {s.nom} {s.prenoms}",
                }
                for s in rows
            ]
        }
    )


@procedures_bp.route("/entities")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def list_entities():
    q = (request.args.get("q") or "").strip()
    branch_filter = _selected_branch_filter(default=0)

    query = _entity_query_scoped(branch_filter)
    if q:
        query = query.filter(Entity.name.ilike(f"%{q}%"))

    entities = query.order_by(Entity.name.asc()).all()

    visible_ids = _visible_branch_ids_for_actor()
    branch_filter_options = Branch.query.filter(Branch.id.in_(visible_ids)).order_by(Branch.name.asc()).all() if visible_ids else []

    return render_template(
        "procedures/entities_list.html",
        entities=entities,
        q=q,
        branch_filter=branch_filter,
        branch_filter_options=branch_filter_options,
    )


@procedures_bp.route("/entities/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def create_entity():
    form = EntityForm()
    branch_filter = _selected_branch_filter(default=0)
    if form.validate_on_submit():
        target_branch_id = branch_filter or _actor_branch_id()
        row = Entity(
            branch_id=target_branch_id,
            name=form.name.data.strip(),
            is_partner=form.is_partner.data,
            notes=(form.notes.data or "").strip() or None,
        )
        db.session.add(row)
        db.session.commit()
        add_audit_log(current_user.id, "entity_create", f"Entite creee: {row.name}", branch_id=target_branch_id, action="entity_create")
        flash("Entite creee.", "success")
        return redirect(url_for("procedures.list_entities", branch_id=target_branch_id or None))
    return render_template("procedures/entity_form.html", form=form, mode="create")


@procedures_bp.route("/entities/<int:entity_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def edit_entity(entity_id):
    row = Entity.query.get_or_404(entity_id)
    _enforce_entity_access(row)
    form = EntityForm(obj=row)
    if form.validate_on_submit():
        row.name = form.name.data.strip()
        row.is_partner = form.is_partner.data
        row.notes = (form.notes.data or "").strip() or None
        if row.branch_id is None:
            row.branch_id = _actor_branch_id()
        db.session.commit()
        add_audit_log(current_user.id, "entity_update", f"Entite modifiee: {row.name}", branch_id=row.branch_id or current_user.branch_id, action="entity_update")
        flash("Entite mise a jour.", "success")
        return redirect(url_for("procedures.list_entities", branch_id=row.branch_id or None))
    return render_template("procedures/entity_form.html", form=form, mode="edit", entity=row)


@procedures_bp.route("/entities/<int:entity_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def delete_entity(entity_id):
    row = Entity.query.get_or_404(entity_id)
    _enforce_entity_access(row)

    linked_schools = School.query.filter_by(entity_id=row.id).count()
    linked_cases = StudyCase.query.filter_by(entity_id=row.id).count()
    if linked_schools > 0 or linked_cases > 0:
        flash("Suppression impossible: cette entite est deja utilisee.", "warning")
        return redirect(url_for("procedures.list_entities", branch_id=row.branch_id or None))

    branch_id = row.branch_id
    db.session.delete(row)
    db.session.commit()
    add_audit_log(current_user.id, "entity_delete", f"Entite supprimee: {row.name}", branch_id=branch_id or current_user.branch_id, action="entity_delete")
    flash("Entite supprimee.", "success")
    return redirect(url_for("procedures.list_entities", branch_id=branch_id or None))

    db.session.delete(row)
    db.session.commit()
    add_audit_log(current_user.id, "entity_delete", f"Entite supprimee: {row.name}", branch_id=current_user.branch_id, action="entity_delete")
    flash("Entite supprimee.", "success")
    return redirect(url_for("procedures.list_entities"))


@procedures_bp.route("/schools")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def list_schools():
    page = max(request.args.get("page", type=int) or 1, 1)
    per_page = 20
    q = (request.args.get("q") or "").strip()
    branch_filter = _selected_branch_filter(default=0)

    query = _school_query_scoped(branch_filter)
    if q:
        query = query.filter(School.name.ilike(f"%{q}%"))

    pagination = query.order_by(School.name.asc()).paginate(page=page, per_page=per_page, error_out=False)
    schools = pagination.items

    visible_ids = _visible_branch_ids_for_actor()
    branch_filter_options = Branch.query.filter(Branch.id.in_(visible_ids)).order_by(Branch.name.asc()).all() if visible_ids else []

    return render_template(
        "procedures/schools_list.html",
        schools=schools,
        pagination=pagination,
        q=q,
        branch_filter=branch_filter,
        branch_filter_options=branch_filter_options,
    )


@procedures_bp.route("/schools/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def create_school():
    form = SchoolForm()
    branch_filter = _selected_branch_filter(default=0)
    form.entity_id.choices = _entity_choices(required=True, branch_id=branch_filter)

    if form.validate_on_submit():
        target_branch_id = branch_filter or _actor_branch_id()
        row = School(
            branch_id=target_branch_id,
            entity_id=form.entity_id.data,
            name=form.name.data.strip(),
            country=(form.country.data or "").strip() or None,
            city=(form.city.data or "").strip() or None,
            website=(form.website.data or "").strip() or None,
        )
        db.session.add(row)
        db.session.commit()
        add_audit_log(current_user.id, "school_create", f"Ecole creee: {row.name}", branch_id=target_branch_id, action="school_create")
        flash("Ecole creee.", "success")
        return redirect(url_for("procedures.list_schools", branch_id=target_branch_id or None))

    return render_template("procedures/school_form.html", form=form, mode="create")


@procedures_bp.route("/schools/<int:school_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def edit_school(school_id):
    row = School.query.get_or_404(school_id)
    _enforce_school_access(row)
    form = SchoolForm(obj=row)
    form.entity_id.choices = _entity_choices(required=True, branch_id=(row.branch_id or _selected_branch_filter(default=0)))

    if form.validate_on_submit():
        row.entity_id = form.entity_id.data
        row.name = form.name.data.strip()
        row.country = (form.country.data or "").strip() or None
        row.city = (form.city.data or "").strip() or None
        row.website = (form.website.data or "").strip() or None
        if row.branch_id is None:
            row.branch_id = _actor_branch_id()
        db.session.commit()
        add_audit_log(current_user.id, "school_update", f"Ecole modifiee: {row.name}", branch_id=row.branch_id or current_user.branch_id, action="school_update")
        flash("Ecole mise a jour.", "success")
        return redirect(url_for("procedures.list_schools", branch_id=row.branch_id or None))

    return render_template("procedures/school_form.html", form=form, mode="edit", school=row)


@procedures_bp.route("/schools/<int:school_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def delete_school(school_id):
    row = School.query.get_or_404(school_id)
    _enforce_school_access(row)

    linked_cases = StudyCase.query.filter_by(school_id=row.id).count()
    if linked_cases > 0:
        flash("Suppression impossible: cette ecole est deja utilisee dans des dossiers.", "warning")
        return redirect(url_for("procedures.list_schools", branch_id=row.branch_id or None))

    branch_id = row.branch_id
    db.session.delete(row)
    db.session.commit()
    add_audit_log(current_user.id, "school_delete", f"Ecole supprimee: {row.name}", branch_id=branch_id or current_user.branch_id, action="school_delete")
    flash("Ecole supprimee.", "success")
    return redirect(url_for("procedures.list_schools", branch_id=branch_id or None))

    db.session.delete(row)
    db.session.commit()
    add_audit_log(current_user.id, "school_delete", f"Ecole supprimee: {row.name}", branch_id=current_user.branch_id, action="school_delete")
    flash("Ecole supprimee.", "success")
    return redirect(url_for("procedures.list_schools"))


@procedures_bp.route("/cases")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def list_cases():
    q = request.args.get("q", "").strip()
    branch_filter = _selected_branch_filter(default=0)

    query = scope_query_by_branch(StudyCase.query, StudyCase)
    visible_ids = _visible_branch_ids_for_actor()
    branch_filter_options = Branch.query.filter(Branch.id.in_(visible_ids)).order_by(Branch.name.asc()).all() if visible_ids else []
    allowed_branch_ids = {b.id for b in branch_filter_options}

    if branch_filter and branch_filter in allowed_branch_ids:
        query = query.filter(StudyCase.branch_id == branch_filter)
    else:
        branch_filter = 0

    if q:
        query = query.join(Student, StudyCase.student_id == Student.id).filter(
            (Student.nom.ilike(f"%{q}%"))
            | (Student.prenoms.ilike(f"%{q}%"))
            | (Student.matricule.ilike(f"%{q}%"))
            | (StudyCase.destination_country.ilike(f"%{q}%"))
        )
    cases = query.order_by(StudyCase.updated_at.desc()).all()
    return render_template(
        "procedures/cases_list.html",
        cases=cases,
        q=q,
        branch_filter=branch_filter,
        branch_filter_options=branch_filter_options,
    )


@procedures_bp.route("/cases/new", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def create_case():
    form = StudyCaseForm()
    form.student_id.choices = _student_choices()
    form.entity_id.choices = _entity_choices(branch_id=_selected_branch_filter(default=0))
    form.school_id.choices = _school_choices(branch_id=_selected_branch_filter(default=0))
    selected_student_label = ""

    pre_student = request.args.get("student_id", type=int)
    if pre_student and request.method == "GET":
        student = _eligible_students_query(include_student_id=pre_student).filter(Student.id == pre_student).first()
        if student:
            form.student_id.data = pre_student
            selected_student_label = f"{student.matricule} - {student.nom} {student.prenoms}"

    if form.validate_on_submit():
        student = Student.query.get_or_404(form.student_id.data)
        if not can_access_branch(student.branch_id):
            abort(403)
        is_eligible = _eligible_students_query().filter(Student.id == student.id).first()
        if not is_eligible:
            flash("Cet etudiant est deja marque a l'etranger et ne peut pas etre repris ici.", "warning")
            return redirect(url_for("procedures.create_case"))

        if form.is_active.data:
            StudyCase.query.filter_by(student_id=student.id, is_active=True).update({"is_active": False})

        row = StudyCase(
            student_id=student.id,
            branch_id=student.branch_id,
            destination_country=(form.destination_country.data or "").strip() or None,
            destination_city=(form.destination_city.data or "").strip() or None,
            entity_id=form.entity_id.data or None,
            school_id=form.school_id.data or None,
            status=form.status.data,
            start_date=form.start_date.data,
            expected_departure_date=form.expected_departure_date.data,
            actual_departure_date=form.actual_departure_date.data,
            arrival_date=form.arrival_date.data,
            is_active=form.is_active.data,
        )
        db.session.add(row)
        db.session.flush()

        stage_names = [
            "Ouverture dossier",
            "Constitution des pieces",
            "Soumission admission",
            "Procedure visa",
            "Preparation depart",
            "Arrivee et installation",
        ]
        for stage_name in stage_names:
            db.session.add(
                CaseStage(
                    case_id=row.id,
                    name=stage_name,
                    status="todo",
                    created_by_user_id=current_user.id,
                )
            )

        db.session.flush()
        _sync_case_stages_with_status(row)
        sync_commission_for_case(row)
        db.session.commit()
        add_audit_log(current_user.id, "study_case_create", f"Dossier cree pour {student.matricule}", student_id=student.id, branch_id=student.branch_id, action="study_case_create")
        flash("Dossier etranger cree.", "success")
        return redirect(url_for("procedures.view_case", case_id=row.id))

    if not selected_student_label and form.student_id.data:
        s = Student.query.get(form.student_id.data)
        if s:
            selected_student_label = f"{s.matricule} - {s.nom} {s.prenoms}"

    return render_template("procedures/case_form.html", form=form, mode="create", selected_student_label=selected_student_label)


@procedures_bp.route("/cases/<int:case_id>")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def view_case(case_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    student = Student.query.get_or_404(case_row.student_id)
    stages = CaseStage.query.filter_by(case_id=case_row.id).order_by(CaseStage.created_at.asc()).all()
    documents = Document.query.filter_by(case_id=case_row.id).order_by(Document.created_at.desc()).all()
    payments = CasePayment.query.filter_by(case_id=case_row.id).order_by(CasePayment.created_at.desc()).all()
    stage_form = CaseStageForm()
    document_form = CaseDocumentForm()
    payment_form = CasePaymentForm()
    status_forms = {stage.id: StageStatusForm(status=stage.status) for stage in stages}
    document_status_forms = {doc.id: CaseDocumentStatusForm(review_status=doc.review_status) for doc in documents}
    payment_forms = {}
    for pay in payments:
        pf = CasePaymentForm()
        pf.label.data = pay.label
        pf.amount.data = f"{float(pay.amount or 0):.2f}"
        pf.currency.data = pay.currency or "EUR"
        pf.paid.data = bool(pay.paid)
        pf.paid_at.data = pay.paid_at.date() if pay.paid_at else None
        payment_forms[pay.id] = pf
    is_closed_case = case_row.status in {"arrive", "installe"}

    return render_template(
        "procedures/case_view.html",
        case_row=case_row,
        student=student,
        stages=stages,
        documents=documents,
        payments=payments,
        stage_form=stage_form,
        document_form=document_form,
        payment_form=payment_form,
        status_forms=status_forms,
        document_status_forms=document_status_forms,
        payment_forms=payment_forms,
        is_closed_case=is_closed_case,
    )


@procedures_bp.route("/cases/<int:case_id>/payments/new", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def add_case_payment(case_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)
    form = CasePaymentForm()
    if not form.validate_on_submit():
        flash("Paiement invalide. Verifie les champs.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    raw_amount = (form.amount.data or "").replace(",", ".").strip()
    try:
        amount = float(raw_amount)
        if amount < 0:
            raise ValueError
    except ValueError:
        flash("Montant invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    row = CasePayment(
        case_id=case_row.id,
        label=form.label.data.strip(),
        amount=amount,
        currency=(form.currency.data or "EUR").strip().upper(),
        paid=bool(form.paid.data),
        paid_at=datetime.utcnow() if form.paid.data else None,
    )
    if form.paid.data and form.paid_at.data:
        row.paid_at = datetime.combine(form.paid_at.data, datetime.min.time())

    db.session.add(row)
    db.session.commit()
    add_audit_log(
        current_user.id,
        "case_payment_create",
        f"Paiement ajoute #{row.id} ({row.label})",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="case_payment_create",
    )
    flash("Paiement ajoute.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/payments/<int:payment_id>/update", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def update_case_payment(case_id, payment_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)
    payment = CasePayment.query.filter_by(id=payment_id, case_id=case_row.id).first_or_404()
    form = CasePaymentForm()
    if not form.validate_on_submit():
        flash("Mise a jour paiement invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    raw_amount = (form.amount.data or "").replace(",", ".").strip()
    try:
        amount = float(raw_amount)
        if amount < 0:
            raise ValueError
    except ValueError:
        flash("Montant invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    payment.label = form.label.data.strip()
    payment.amount = amount
    payment.currency = (form.currency.data or "EUR").strip().upper()
    payment.paid = bool(form.paid.data)
    if payment.paid:
        payment.paid_at = datetime.combine(form.paid_at.data, datetime.min.time()) if form.paid_at.data else datetime.utcnow()
    else:
        payment.paid_at = None

    db.session.commit()
    add_audit_log(
        current_user.id,
        "case_payment_update",
        f"Paiement maj #{payment.id} ({payment.label})",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="case_payment_update",
    )
    flash("Paiement mis a jour.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/mark-status/<status>", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def quick_mark_case_status(case_id, status):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)
    student = Student.query.get_or_404(case_row.student_id)

    allowed = {"parti", "arrive", "installe"}
    if status not in allowed:
        flash("Statut rapide invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    case_row.status = status
    # Un etudiant marque parti/arrive/installe doit rester sur un dossier actif.
    case_row.is_active = True
    StudyCase.query.filter(
        StudyCase.student_id == case_row.student_id,
        StudyCase.id != case_row.id,
        StudyCase.is_active.is_(True),
    ).update({"is_active": False})
    now_date = datetime.utcnow().date()
    if status == "parti" and not case_row.actual_departure_date:
        case_row.actual_departure_date = now_date
    if status in {"arrive", "installe"} and not case_row.arrival_date:
        case_row.arrival_date = now_date

    # Synchronise le statut global etudiant pour les dashboards.
    if status == "parti":
        student.statut_global = "parti"
    elif status in {"arrive", "installe"}:
        student.statut_global = "sur_place"

    _sync_case_stages_with_status(case_row)
    sync_commission_for_case(case_row)
    db.session.commit()
    add_audit_log(
        current_user.id,
        "study_case_quick_status",
        f"Dossier #{case_row.id} -> {status}",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="study_case_quick_status",
    )
    flash(f"Dossier marque: {status}.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def edit_case(case_id):
    row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(row)

    form = StudyCaseForm(obj=row)
    form.student_id.choices = _student_choices(include_student_id=row.student_id)
    form.entity_id.choices = _entity_choices(branch_id=_selected_branch_filter(default=0))
    form.school_id.choices = _school_choices(branch_id=_selected_branch_filter(default=0))

    if form.validate_on_submit():
        student = Student.query.get_or_404(form.student_id.data)
        if not can_access_branch(student.branch_id):
            abort(403)
        is_eligible = _eligible_students_query(include_student_id=row.student_id).filter(Student.id == student.id).first()
        if not is_eligible:
            flash("Cet etudiant est deja marque a l'etranger et ne peut pas etre selectionne.", "warning")
            return redirect(url_for("procedures.edit_case", case_id=row.id))

        if form.is_active.data:
            StudyCase.query.filter(
                StudyCase.student_id == student.id,
                StudyCase.id != row.id,
                StudyCase.is_active.is_(True),
            ).update({"is_active": False})

        row.student_id = student.id
        row.branch_id = student.branch_id
        row.destination_country = (form.destination_country.data or "").strip() or None
        row.destination_city = (form.destination_city.data or "").strip() or None
        row.entity_id = form.entity_id.data or None
        row.school_id = form.school_id.data or None
        row.status = form.status.data
        row.start_date = form.start_date.data
        row.expected_departure_date = form.expected_departure_date.data
        row.actual_departure_date = form.actual_departure_date.data
        row.arrival_date = form.arrival_date.data
        row.is_active = form.is_active.data
        if row.status in {"parti", "arrive", "installe"}:
            row.is_active = True
            student.statut_global = "parti" if row.status == "parti" else "sur_place"
            StudyCase.query.filter(
                StudyCase.student_id == student.id,
                StudyCase.id != row.id,
                StudyCase.is_active.is_(True),
            ).update({"is_active": False})
        _sync_case_stages_with_status(row)
        sync_commission_for_case(row)
        db.session.commit()

        add_audit_log(current_user.id, "study_case_update", f"Dossier #{row.id} modifie", student_id=student.id, branch_id=student.branch_id, action="study_case_update")
        flash("Dossier mis a jour.", "success")
        return redirect(url_for("procedures.view_case", case_id=row.id))

    selected_student_label = ""
    if row.student:
        selected_student_label = f"{row.student.matricule} - {row.student.nom} {row.student.prenoms}"

    return render_template("procedures/case_form.html", form=form, mode="edit", case_row=row, selected_student_label=selected_student_label)


@procedures_bp.route("/cases/<int:case_id>/delete", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def delete_case(case_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)
    student_id = case_row.student_id
    branch_id = case_row.branch_id
    was_active = bool(case_row.is_active)

    CaseStage.query.filter_by(case_id=case_row.id).delete(synchronize_session=False)
    Document.query.filter_by(case_id=case_row.id).delete(synchronize_session=False)
    ArrivalSupport.query.filter_by(case_id=case_row.id).delete(synchronize_session=False)
    CasePayment.query.filter_by(case_id=case_row.id).delete(synchronize_session=False)
    CommissionRecord.query.filter_by(case_id=case_row.id).delete(synchronize_session=False)
    db.session.delete(case_row)

    if was_active:
        fallback_case = (
            StudyCase.query.filter(StudyCase.student_id == student_id, StudyCase.id != case_id)
            .order_by(StudyCase.updated_at.desc())
            .first()
        )
        if fallback_case:
            fallback_case.is_active = True

    db.session.commit()
    add_audit_log(
        current_user.id,
        "study_case_delete",
        f"Dossier #{case_id} supprime",
        student_id=student_id,
        branch_id=branch_id,
        action="study_case_delete",
    )
    flash("Dossier supprime.", "success")
    return redirect(url_for("procedures.list_cases"))


@procedures_bp.route("/cases/<int:case_id>/stages/new", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def add_stage(case_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    form = CaseStageForm()
    if not form.validate_on_submit():
        flash("Etape invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    stage = CaseStage(
        case_id=case_row.id,
        name=form.name.data.strip(),
        status=form.status.data,
        due_date=form.due_date.data,
        notes=(form.notes.data or "").strip() or None,
        created_by_user_id=current_user.id,
    )
    if stage.status == "done":
        stage.completed_at = datetime.utcnow()

    db.session.add(stage)
    db.session.commit()
    add_audit_log(current_user.id, "case_stage_create", f"Etape ajoutee sur dossier #{case_row.id}", student_id=case_row.student_id, branch_id=case_row.branch_id, action="case_stage_create")
    flash("Etape ajoutee.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/stages/<int:stage_id>/status", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def update_stage_status(case_id, stage_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    stage = CaseStage.query.filter_by(id=stage_id, case_id=case_row.id).first_or_404()
    form = StageStatusForm()
    if not form.validate_on_submit():
        flash("Statut invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    stage.status = form.status.data
    stage.completed_at = datetime.utcnow() if stage.status == "done" else None
    db.session.commit()

    add_audit_log(current_user.id, "case_stage_update", f"Etape #{stage.id} -> {stage.status}", student_id=case_row.student_id, branch_id=case_row.branch_id, action="case_stage_update")
    flash("Statut etape mis a jour.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/documents/upload", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def upload_case_document(case_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    form = CaseDocumentForm()
    if not form.validate_on_submit():
        flash("Upload document invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    file_obj = form.file.data
    upload_dir = os.path.join(current_app.config["FORM_UPLOAD_DIR"], "study_cases", str(case_row.id))
    try:
        stored_filename = save_uploaded_file(file_obj, upload_dir, current_app.config["ALLOWED_DOC_EXTENSIONS"])
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    row = Document(
        student_id=case_row.student_id,
        case_id=case_row.id,
        uploaded_by_user_id=current_user.id,
        filename=file_obj.filename,
        stored_path=os.path.join(upload_dir, stored_filename),
        doc_type=form.doc_type.data.strip(),
        notes=(form.notes.data or "").strip() or None,
        review_status="recu",
    )
    db.session.add(row)
    db.session.commit()
    add_audit_log(
        current_user.id,
        "case_document_upload",
        f"Document charge sur dossier #{case_row.id}",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="case_document_upload",
    )
    flash("Document ajoute au dossier.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/documents/<int:document_id>/status", methods=["POST"])
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def update_case_document_status(case_id, document_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    document = Document.query.filter_by(id=document_id, case_id=case_row.id).first_or_404()
    form = CaseDocumentStatusForm()
    if not form.validate_on_submit():
        flash("Statut document invalide.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    document.review_status = form.review_status.data
    db.session.commit()
    add_audit_log(
        current_user.id,
        "case_document_status",
        f"Document #{document.id} -> {document.review_status}",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="case_document_status",
    )
    flash("Statut document mis a jour.", "success")
    return redirect(url_for("procedures.view_case", case_id=case_row.id))


@procedures_bp.route("/cases/<int:case_id>/documents/<int:document_id>/download")
@login_required
@role_required("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE", "IT")
def download_case_document(case_id, document_id):
    case_row = StudyCase.query.get_or_404(case_id)
    _enforce_case_access(case_row)

    document = Document.query.filter_by(id=document_id, case_id=case_row.id).first_or_404()
    if not os.path.exists(document.stored_path):
        flash("Fichier introuvable sur le serveur.", "danger")
        return redirect(url_for("procedures.view_case", case_id=case_row.id))

    add_audit_log(
        current_user.id,
        "case_document_download",
        f"Telechargement document #{document.id}",
        student_id=case_row.student_id,
        branch_id=case_row.branch_id,
        action="case_document_download",
    )
    return send_file(document.stored_path, as_attachment=True, download_name=document.filename)















