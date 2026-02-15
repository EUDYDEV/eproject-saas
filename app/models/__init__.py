from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint

from app.extensions import db, login_manager


class Branch(db.Model):
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=True)
    country_code = db.Column(db.String(10), nullable=False)
    city = db.Column(db.String(120), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    logo_url = db.Column(db.String(255), nullable=True)
    website_url = db.Column(db.String(255), nullable=True)
    timezone = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    avatar_path = db.Column(db.String(255), nullable=True)
    email_signature = db.Column(db.Text, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    platform_role = db.Column(db.String(50), nullable=True, default=None)
    role = db.Column(db.String(30), nullable=False, default="EMPLOYEE")
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    branch = db.relationship("Branch", lazy=True)
    memberships = db.relationship("Membership", backref="user", lazy=True, cascade="all, delete-orphan")


class Membership(db.Model):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "branch_id", name="uq_membership_user_branch"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="STAFF")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    branch = db.relationship("Branch", lazy=True)


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    matricule = db.Column(db.String(50), unique=True, nullable=False)
    nom = db.Column(db.String(120), nullable=False)
    prenoms = db.Column(db.String(160), nullable=False)
    sexe = db.Column(db.String(20), nullable=False)
    date_naissance = db.Column(db.Date, nullable=True)
    email = db.Column(db.String(120), nullable=True)
    procedure_email = db.Column(db.String(120), nullable=True)
    procedure_email_password = db.Column(db.String(255), nullable=True)
    telephone = db.Column(db.String(40), nullable=True)
    adresse = db.Column(db.Text, nullable=True)
    filiere = db.Column(db.String(120), nullable=False)
    niveau = db.Column(db.String(80), nullable=False)
    promotion = db.Column(db.String(20), nullable=False)
    destination_wished_country = db.Column(db.String(120), nullable=True)
    destination_wished_city = db.Column(db.String(120), nullable=True)
    program_wished = db.Column(db.String(255), nullable=True)
    notes_projet = db.Column(db.Text, nullable=True)
    statut_global = db.Column(db.String(30), default="prospect", nullable=False)
    photo_path = db.Column(db.String(255), nullable=True)
    statut = db.Column(db.String(20), default="actif", nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    branch = db.relationship("Branch", lazy=True)
    guardians = db.relationship("Guardian", backref="student", lazy=True, cascade="all, delete-orphan")
    documents = db.relationship("StudentDocument", backref="student", lazy=True, cascade="all, delete-orphan")


class StudentAuth(db.Model):
    __tablename__ = "student_auth"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    student = db.relationship("Student", lazy=True)


class Guardian(db.Model):
    __tablename__ = "guardians"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    nom = db.Column(db.String(120), nullable=False)
    prenoms = db.Column(db.String(160), nullable=False)
    lien_parente = db.Column(db.String(80), nullable=False)
    telephone = db.Column(db.String(40), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    adresse = db.Column(db.Text, nullable=True)
    contact_urgence = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentDocument(db.Model):
    __tablename__ = "student_documents"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    target_folder = db.Column(db.String(20), nullable=False)
    document_type = db.Column(db.String(80), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentDocumentFolder(db.Model):
    __tablename__ = "student_document_folders"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    folder_name = db.Column(db.String(40), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class StudentCV(db.Model):
    __tablename__ = "student_cvs"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False, unique=True)
    profile_text = db.Column(db.Text, nullable=True)
    contact_details = db.Column(db.Text, nullable=True)
    hobbies = db.Column(db.Text, nullable=True)
    languages = db.Column(db.Text, nullable=True)
    skills = db.Column(db.Text, nullable=True)
    education = db.Column(db.Text, nullable=True)
    awards = db.Column(db.Text, nullable=True)
    references_text = db.Column(db.Text, nullable=True)
    social_links = db.Column(db.Text, nullable=True)
    professional_experience = db.Column(db.Text, nullable=True)
    extra_experience = db.Column(db.Text, nullable=True)
    software = db.Column(db.Text, nullable=True)
    show_hobbies = db.Column(db.Boolean, default=True, nullable=False)
    show_languages = db.Column(db.Boolean, default=True, nullable=False)
    show_skills = db.Column(db.Boolean, default=True, nullable=False)
    show_education = db.Column(db.Boolean, default=True, nullable=False)
    show_professional_experience = db.Column(db.Boolean, default=True, nullable=False)
    show_extra_experience = db.Column(db.Boolean, default=False, nullable=False)
    show_software = db.Column(db.Boolean, default=True, nullable=False)
    show_social_links = db.Column(db.Boolean, default=True, nullable=False)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    student = db.relationship("Student", lazy=True)
    updated_by = db.relationship("User", lazy=True)


class Entity(db.Model):
    __tablename__ = "entities"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    is_partner = db.Column(db.Boolean, default=False, nullable=False)
    logo_path = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    branch = db.relationship("Branch", lazy=True)


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    entity_id = db.Column(db.Integer, db.ForeignKey("entities.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    country = db.Column(db.String(120), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    website = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    entity = db.relationship("Entity", lazy=True)
    branch = db.relationship("Branch", lazy=True)


class StudyCase(db.Model):
    __tablename__ = "study_cases"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    destination_country = db.Column(db.String(120), nullable=True)
    destination_city = db.Column(db.String(120), nullable=True)
    entity_id = db.Column(db.Integer, db.ForeignKey("entities.id"), nullable=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True)
    status = db.Column(db.String(40), default="nouveau", nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    expected_departure_date = db.Column(db.Date, nullable=True)
    actual_departure_date = db.Column(db.Date, nullable=True)
    arrival_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    student = db.relationship("Student", lazy=True)
    branch = db.relationship("Branch", lazy=True)
    entity = db.relationship("Entity", lazy=True)
    school = db.relationship("School", lazy=True)


class CaseStage(db.Model):
    __tablename__ = "case_stages"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("study_cases.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=True)
    status = db.Column(db.String(20), default="todo", nullable=False)
    due_date = db.Column(db.Date, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    study_case = db.relationship("StudyCase", lazy=True)
    created_by = db.relationship("User", lazy=True)


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey("study_cases.id"), nullable=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(255), nullable=False)
    doc_type = db.Column(db.String(80), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    review_status = db.Column(db.String(20), default="recu", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ArrivalSupport(db.Model):
    __tablename__ = "arrival_support"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("study_cases.id"), nullable=False)
    host_entity_name = db.Column(db.String(255), nullable=True)
    contact_name = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    lodging_status = db.Column(db.String(20), nullable=True)
    pickup_status = db.Column(db.String(20), nullable=True)
    mentor_assigned = db.Column(db.String(255), nullable=True)
    followup_notes = db.Column(db.Text, nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CasePayment(db.Model):
    __tablename__ = "case_payments"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("study_cases.id"), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default="EUR", nullable=False)
    paid = db.Column(db.Boolean, default=False, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    receipt_file = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CommissionRule(db.Model):
    __tablename__ = "commission_rules"

    id = db.Column(db.Integer, primary_key=True)
    entity_id = db.Column(db.Integer, db.ForeignKey("entities.id"), nullable=False)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), nullable=True)
    amount_per_student = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default="EUR", nullable=False)
    trigger_status = db.Column(db.String(20), default="installe", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    entity = db.relationship("Entity", lazy=True)
    school = db.relationship("School", lazy=True)


class CommissionRecord(db.Model):
    __tablename__ = "commission_records"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("study_cases.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)

    study_case = db.relationship("StudyCase", lazy=True)


class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    description_html = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(255), nullable=True)
    timezone = db.Column(db.String(80), nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    day_start_time = db.Column(db.Time, nullable=True)
    day_end_time = db.Column(db.Time, nullable=True)
    slot_minutes = db.Column(db.Integer, default=30, nullable=False)
    max_per_day = db.Column(db.Integer, default=10, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class EventSlot(db.Model):
    __tablename__ = "event_slots"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=False)
    capacity = db.Column(db.Integer, default=1, nullable=False)
    booked_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event = db.relationship("Event", lazy=True)


class Booking(db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("event_slots.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    invite_token_id = db.Column(db.Integer, db.ForeignKey("invite_tokens.id"), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(80), nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event_slot = db.relationship("EventSlot", lazy=True)
    student = db.relationship("Student", lazy=True)
    invite_token = db.relationship("InviteToken", lazy=True)


class InviteToken(db.Model):
    __tablename__ = "invite_tokens"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    token = db.Column(db.String(255), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    event = db.relationship("Event", lazy=True)
    student = db.relationship("Student", lazy=True)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    type_event = db.Column(db.String(80), nullable=False)
    action = db.Column(db.String(80), nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    student = db.relationship("Student", lazy=True)
    user = db.relationship("User", lazy=True)
    branch = db.relationship("Branch", lazy=True)


class SMTPSetting(db.Model):
    __tablename__ = "smtp_settings"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=False, default=587)
    username = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    use_tls = db.Column(db.Boolean, default=True, nullable=False)
    from_email = db.Column(db.String(255), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EmailTemplate(db.Model):
    __tablename__ = "email_templates"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    body_text = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EmailDispatch(db.Model):
    __tablename__ = "email_dispatches"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    template_id = db.Column(db.Integer, db.ForeignKey("email_templates.id"), nullable=True)
    recipient_type = db.Column(db.String(30), nullable=False)
    recipient_email = db.Column(db.String(255), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    guardian_id = db.Column(db.Integer, db.ForeignKey("guardians.id"), nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    to_email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    error = db.Column(db.Text, nullable=True)
    sent_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class DynamicForm(db.Model):
    __tablename__ = "dynamic_forms"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class DynamicFormField(db.Model):
    __tablename__ = "dynamic_form_fields"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("dynamic_forms.id"), nullable=False)
    field_key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(255), nullable=False)
    field_type = db.Column(db.String(20), nullable=False)
    is_required = db.Column(db.Boolean, default=False, nullable=False)
    options_json = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class FormToken(db.Model):
    __tablename__ = "form_tokens"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("dynamic_forms.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    guardian_id = db.Column(db.Integer, db.ForeignKey("guardians.id"), nullable=True)
    email = db.Column(db.String(255), nullable=False)
    token = db.Column(db.String(255), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, default=False, nullable=False)
    max_responses = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class FormResponse(db.Model):
    __tablename__ = "form_responses"

    id = db.Column(db.Integer, primary_key=True)
    form_id = db.Column(db.Integer, db.ForeignKey("dynamic_forms.id"), nullable=False)
    token_id = db.Column(db.Integer, db.ForeignKey("form_tokens.id"), nullable=False)
    responder_email = db.Column(db.String(255), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class FormResponseItem(db.Model):
    __tablename__ = "form_response_items"

    id = db.Column(db.Integer, primary_key=True)
    response_id = db.Column(db.Integer, db.ForeignKey("form_responses.id"), nullable=False)
    field_id = db.Column(db.Integer, db.ForeignKey("dynamic_form_fields.id"), nullable=False)
    value_text = db.Column(db.Text, nullable=True)
    value_file_path = db.Column(db.String(255), nullable=True)


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=True)
    guardian_id = db.Column(db.Integer, db.ForeignKey("guardians.id"), nullable=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    motif = db.Column(db.String(255), nullable=False)
    requested_date = db.Column(db.Date, nullable=False)
    requested_slot = db.Column(db.String(80), nullable=False)
    responder_name = db.Column(db.String(255), nullable=True)
    responder_email = db.Column(db.String(255), nullable=True)
    responder_phone = db.Column(db.String(80), nullable=True)
    commentaire = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    admin_comment = db.Column(db.Text, nullable=True)
    processed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    processed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PortalSetting(db.Model):
    __tablename__ = "portal_settings"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True)
    event_title = db.Column(db.String(255), nullable=True)
    orientation_date = db.Column(db.String(120), nullable=True)
    orientation_address = db.Column(db.String(255), nullable=True)
    orientation_phone = db.Column(db.String(80), nullable=True)
    representative_name = db.Column(db.String(255), nullable=True)
    appointment_slots = db.Column(db.Text, nullable=True)
    max_appointments_per_day = db.Column(db.Integer, nullable=False, default=10)
    site_name = db.Column(db.String(120), nullable=True)
    site_tagline = db.Column(db.String(255), nullable=True)
    site_footer_text = db.Column(db.String(255), nullable=True)
    site_logo_url = db.Column(db.String(255), nullable=True)
    plan_starter_price = db.Column(db.Float, nullable=False, default=0.0)
    plan_pro_price = db.Column(db.Float, nullable=False, default=0.0)
    plan_enterprise_price = db.Column(db.Float, nullable=False, default=0.0)
    plan_currency = db.Column(db.String(10), nullable=False, default="XOF")
    payment_link = db.Column(db.String(500), nullable=True)
    payment_link_starter = db.Column(db.String(500), nullable=True)
    payment_link_pro = db.Column(db.String(500), nullable=True)
    payment_link_enterprise = db.Column(db.String(500), nullable=True)
    billing_sender_email = db.Column(db.String(255), nullable=True, default="eudyproject@gmail.com")
    expiry_notice_days = db.Column(db.Integer, nullable=False, default=7)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AgencySubscription(db.Model):
    __tablename__ = "agency_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=False, unique=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    plan_code = db.Column(db.String(30), nullable=False, default="starter")
    amount = db.Column(db.Float, nullable=False, default=0.0)
    currency = db.Column(db.String(10), nullable=False, default="XOF")
    status = db.Column(db.String(20), nullable=False, default="pending")
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    payment_reference = db.Column(db.String(255), nullable=True)
    last_warning_sent_at = db.Column(db.DateTime, nullable=True)
    last_expired_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    branch = db.relationship("Branch", lazy=True)
    owner_user = db.relationship("User", lazy=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))



