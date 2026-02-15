import os
from datetime import datetime
from functools import wraps

from argon2 import PasswordHasher
from flask import Blueprint, current_app, flash, redirect, render_template, send_file, session, url_for

from app.extensions import db
from app.models import Appointment, Booking, CasePayment, CaseStage, Document, EventSlot, Student, StudentAuth, StudyCase
from app.student_portal.forms import StudentChangePasswordForm, StudentLoginForm, StudentPortalDocumentForm, StudentProfileForm
from app.utils.files import save_uploaded_file


student_portal_bp = Blueprint("student_portal", __name__, url_prefix="/student")
password_hasher = PasswordHasher()


SESSION_KEY = "student_portal_id"


def get_current_student_auth():
    sid = session.get(SESSION_KEY)
    if not sid:
        return None
    return StudentAuth.query.get(sid)


def get_current_student():
    auth = get_current_student_auth()
    if not auth:
        return None
    return Student.query.get(auth.student_id)


def student_photo_url(student):
    if student and student.photo_path:
        return url_for("static", filename=f"uploads/photos/{student.photo_path}")
    return ""


@student_portal_bp.app_context_processor
def inject_student_portal_context():
    student = get_current_student()
    return {
        "portal_student": student,
        "portal_student_photo_url": student_photo_url(student),
    }


def student_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        auth = get_current_student_auth()
        if not auth:
            return redirect(url_for("student_portal.login"))
        return view(*args, **kwargs)

    return wrapper


@student_portal_bp.route("/login", methods=["GET", "POST"])
def login():
    if get_current_student_auth():
        return redirect(url_for("student_portal.dashboard"))

    form = StudentLoginForm()
    if form.validate_on_submit():
        student = Student.query.filter_by(matricule=form.matricule.data.strip()).first()
        if not student:
            flash("Matricule introuvable.", "danger")
            return render_template("student_portal/login.html", form=form)

        auth = StudentAuth.query.filter_by(student_id=student.id).first()
        if not auth:
            auth = StudentAuth(
                student_id=student.id,
                password_hash=password_hasher.hash(f"Temp-{student.id}-{datetime.utcnow().timestamp()}"),
                must_change_password=True,
            )
            db.session.add(auth)

        # Regle metier:
        # - Premiere connexion: matricule seul autorise (must_change_password=True)
        # - Ensuite: mot de passe obligatoire
        if not auth.must_change_password:
            provided = (form.password.data or "").strip()
            if not provided:
                flash("Mot de passe requis pour ce matricule.", "danger")
                return render_template("student_portal/login.html", form=form)
            try:
                if not password_hasher.verify(auth.password_hash, provided):
                    flash("Mot de passe invalide.", "danger")
                    return render_template("student_portal/login.html", form=form)
            except Exception:
                flash("Mot de passe invalide.", "danger")
                return render_template("student_portal/login.html", form=form)

        auth.last_login = datetime.utcnow()
        db.session.commit()
        session[SESSION_KEY] = auth.id

        if auth.must_change_password:
            flash("Bienvenue. Definis maintenant ton mot de passe.", "warning")
            return redirect(url_for("student_portal.change_password"))
        return redirect(url_for("student_portal.dashboard"))

    return render_template("student_portal/login.html", form=form)


@student_portal_bp.route("/logout")
def logout():
    session.pop(SESSION_KEY, None)
    flash("Session etudiant fermee.", "success")
    return redirect(url_for("student_portal.login"))


@student_portal_bp.route("/change-password", methods=["GET", "POST"])
@student_login_required
def change_password():
    auth = get_current_student_auth()
    form = StudentChangePasswordForm()
    if form.validate_on_submit():
        auth.password_hash = password_hasher.hash(form.new_password.data)
        auth.must_change_password = False
        db.session.commit()
        flash("Mot de passe modifie.", "success")
        return redirect(url_for("student_portal.dashboard"))

    return render_template("student_portal/change_password.html", form=form)


@student_portal_bp.route("/profile/update", methods=["POST"])
@student_login_required
def update_profile():
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    form = StudentProfileForm()
    if not form.validate_on_submit():
        flash("Profil invalide. Verifie les champs.", "danger")
        return redirect(url_for("student_portal.dashboard"))

    student.nom = form.nom.data.strip()
    student.prenoms = form.prenoms.data.strip()
    student.email = (form.email.data or "").strip().lower() or None
    student.telephone = (form.telephone.data or "").strip() or None
    student.adresse = (form.adresse.data or "").strip() or None

    if form.avatar.data:
        try:
            student.photo_path = save_uploaded_file(
                form.avatar.data,
                current_app.config["PHOTO_UPLOAD_DIR"],
                current_app.config["ALLOWED_IMAGE_EXTENSIONS"],
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("student_portal.dashboard"))

    db.session.commit()
    flash("Profil mis a jour.", "success")
    return redirect(url_for("student_portal.dashboard"))


@student_portal_bp.route("/rdv/<int:appointment_id>/confirm", methods=["POST"])
@student_login_required
def confirm_appointment(appointment_id):
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    rdv = Appointment.query.filter_by(id=appointment_id, student_id=student.id).first_or_404()
    if rdv.status not in ("confirmed", "done", "cancelled"):
        rdv.status = "confirmed"
        db.session.commit()
        flash("Rendez-vous confirme.", "success")
    else:
        flash("Ce rendez-vous est deja traite.", "info")
    return redirect(url_for("student_portal.dashboard", _anchor="rdv"))


@student_portal_bp.route("/bookings/<int:booking_id>/confirm", methods=["POST"])
@student_login_required
def confirm_booking(booking_id):
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    booking = Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id).filter(Booking.id == booking_id, Booking.student_id == student.id).first_or_404()
    if booking.status != "confirmed":
        booking.status = "confirmed"
        db.session.commit()
        flash("Reservation confirmee.", "success")
    else:
        flash("Reservation deja confirmee.", "info")
    return redirect(url_for("student_portal.dashboard", _anchor="rdv"))


@student_portal_bp.route("/")
@student_login_required
def dashboard():
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    active_case = StudyCase.query.filter_by(student_id=student.id, is_active=True).order_by(StudyCase.id.desc()).first()
    stages = []
    payments = []
    if active_case:
        stages = CaseStage.query.filter_by(case_id=active_case.id).order_by(CaseStage.created_at.asc()).all()
        payments = CasePayment.query.filter_by(case_id=active_case.id).order_by(CasePayment.created_at.desc()).all()

    rdv_upcoming = Appointment.query.filter(
        Appointment.student_id == student.id,
        Appointment.requested_date >= datetime.utcnow().date(),
    ).order_by(Appointment.requested_date.asc()).limit(10).all()

    booking_upcoming = (
        Booking.query.join(EventSlot, Booking.slot_id == EventSlot.id)
        .filter(
            Booking.student_id == student.id,
            EventSlot.start_datetime >= datetime.utcnow(),
        )
        .order_by(EventSlot.start_datetime.asc())
        .limit(10)
        .all()
    )

    documents = Document.query.filter_by(student_id=student.id).order_by(Document.created_at.desc()).all()
    doc_form = StudentPortalDocumentForm()
    profile_form = StudentProfileForm(obj=student)

    stage_total = len(stages)
    stage_done = len([s for s in stages if s.status == "done"])
    stage_doing = len([s for s in stages if s.status == "doing"])
    stage_todo = max(stage_total - stage_done - stage_doing, 0)
    progress_pct = int((stage_done / stage_total) * 100) if stage_total else 0

    total_amount = float(sum(float(p.amount or 0) for p in payments))
    paid_amount = float(sum(float(p.amount or 0) for p in payments if p.paid))
    due_amount = max(total_amount - paid_amount, 0.0)

    status_to_readiness = {
        "nouveau": 10,
        "dossier_en_cours": 30,
        "admission": 50,
        "visa": 70,
        "billet": 85,
        "parti": 100,
        "arrive": 100,
        "installe": 100,
    }
    readiness_score = status_to_readiness.get((active_case.status if active_case else ""), progress_pct)
    will_travel = readiness_score >= 70

    chart_data = {
        "stage_labels": ["Done", "Doing", "Todo"],
        "stage_values": [stage_done, stage_doing, stage_todo],
        "payment_labels": ["Paye", "Reste"],
        "payment_values": [round(paid_amount, 2), round(due_amount, 2)],
        "readiness_score": readiness_score,
    }

    return render_template(
        "student_portal/dashboard.html",
        student=student,
        active_case=active_case,
        stages=stages,
        rdv_upcoming=rdv_upcoming,
        booking_upcoming=booking_upcoming,
        documents=documents,
        payments=payments,
        doc_form=doc_form,
        profile_form=profile_form,
        progress_pct=progress_pct,
        stage_done=stage_done,
        stage_total=stage_total,
        total_amount=round(total_amount, 2),
        paid_amount=round(paid_amount, 2),
        due_amount=round(due_amount, 2),
        readiness_score=readiness_score,
        will_travel=will_travel,
        chart_data=chart_data,
    )


@student_portal_bp.route("/documents/upload", methods=["POST"])
@student_login_required
def upload_document():
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    form = StudentPortalDocumentForm()
    if not form.validate_on_submit():
        flash("Upload invalide.", "danger")
        return redirect(url_for("student_portal.dashboard"))

    upload_dir = os.path.join(current_app.config["FORM_UPLOAD_DIR"], "student_portal", str(student.id))
    file_obj = form.file.data
    try:
        stored_filename = save_uploaded_file(file_obj, upload_dir, {"pdf", "jpg", "jpeg", "png", "docx"})
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("student_portal.dashboard"))

    active_case = StudyCase.query.filter_by(student_id=student.id, is_active=True).order_by(StudyCase.id.desc()).first()

    row = Document(
        student_id=student.id,
        case_id=active_case.id if active_case else None,
        uploaded_by_user_id=None,
        filename=file_obj.filename,
        stored_path=os.path.join(upload_dir, stored_filename),
        doc_type=form.doc_type.data.strip(),
        notes=(form.notes.data or "").strip() or None,
        review_status="recu",
    )
    db.session.add(row)
    db.session.commit()
    flash("Document envoye. Statut: recu.", "success")
    return redirect(url_for("student_portal.dashboard", _anchor="documents"))


@student_portal_bp.route("/documents/<int:document_id>/download")
@student_login_required
def download_document(document_id):
    student = get_current_student()
    if student is None:
        return redirect(url_for("student_portal.login"))

    document = Document.query.filter_by(id=document_id, student_id=student.id).first_or_404()
    if not os.path.exists(document.stored_path):
        flash("Fichier introuvable.", "danger")
        return redirect(url_for("student_portal.dashboard"))
    return send_file(document.stored_path, as_attachment=True, download_name=document.filename)
