from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.forms_module.forms import DynamicFieldForm, DynamicFormBuilderForm, TokenGenerationForm
from app.models import DynamicForm, DynamicFormField, FormResponse, FormResponseItem, FormToken
from app.utils.audit import add_audit_log
from app.utils.authz import role_required
from app.utils.files import save_uploaded_file
from app.utils.tokens import default_expiry, generate_token_value


forms_bp = Blueprint("forms_module", __name__, url_prefix="/forms")


@forms_bp.route("/")
@login_required
@role_required("ADMIN", "INFORMATICIEN", "EMPLOYEE")
def list_forms():
    forms = DynamicForm.query.order_by(DynamicForm.id.desc()).all()
    return render_template("forms/list.html", forms=forms)


@forms_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "INFORMATICIEN", "EMPLOYEE")
def create_form():
    form = DynamicFormBuilderForm()
    if form.validate_on_submit():
        row = DynamicForm(title=form.title.data.strip(), description=form.description.data, created_by=current_user.id)
        db.session.add(row)
        db.session.commit()
        add_audit_log(current_user.id, "form_create", f"Form #{row.id}")
        return redirect(url_for("forms_module.manage_fields", form_id=row.id))
    return render_template("forms/form_create.html", form=form)


@forms_bp.route("/<int:form_id>/fields", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "INFORMATICIEN", "EMPLOYEE")
def manage_fields(form_id):
    current_form = DynamicForm.query.get_or_404(form_id)
    field_form = DynamicFieldForm()
    if field_form.validate_on_submit():
        options_raw = (field_form.options_json.data or "").strip()
        row = DynamicFormField(
            form_id=current_form.id,
            field_key=field_form.field_key.data.strip(),
            label=field_form.label.data.strip(),
            field_type=field_form.field_type.data,
            is_required=field_form.is_required.data == "1",
            options_json=options_raw if field_form.field_type.data == "select" else None,
            sort_order=field_form.sort_order.data,
        )
        db.session.add(row)
        db.session.commit()
        add_audit_log(current_user.id, "form_field_create", f"Form {current_form.id} champ {row.field_key}")
        return redirect(url_for("forms_module.manage_fields", form_id=current_form.id))

    fields = DynamicFormField.query.filter_by(form_id=current_form.id).order_by(DynamicFormField.sort_order.asc()).all()
    return render_template("forms/fields.html", current_form=current_form, field_form=field_form, fields=fields)


@forms_bp.route("/tokens", methods=["GET", "POST"])
@login_required
@role_required("ADMIN", "INFORMATICIEN", "SECRETAIRE")
def generate_token():
    form = TokenGenerationForm()
    form.form_id.choices = [(f.id, f.title) for f in DynamicForm.query.order_by(DynamicForm.title.asc()).all()]

    token_link = None
    if form.validate_on_submit():
        token = FormToken(
            form_id=form.form_id.data,
            email=form.email.data.strip().lower(),
            student_id=form.student_id.data or None,
            guardian_id=form.guardian_id.data or None,
            token=generate_token_value(),
            expires_at=default_expiry(form.expires_days.data),
        )
        db.session.add(token)
        db.session.commit()
        token_link = url_for("forms_module.public_form", token_value=token.token, _external=True)
        add_audit_log(current_user.id, "form_token_create", f"Token cree pour {token.email}")
        flash("Lien genere.", "success")

    return render_template("forms/token_generate.html", form=form, token_link=token_link)


@forms_bp.route("/public/<token_value>", methods=["GET", "POST"])
def public_form(token_value):
    token = FormToken.query.filter_by(token=token_value).first_or_404()
    if token.is_used or token.expires_at < datetime.utcnow():
        return render_template("forms/public_expired.html")

    current_form = DynamicForm.query.get_or_404(token.form_id)
    fields = DynamicFormField.query.filter_by(form_id=current_form.id).order_by(DynamicFormField.sort_order.asc()).all()

    if request.method == "POST":
        response = FormResponse(form_id=current_form.id, token_id=token.id, responder_email=token.email)
        db.session.add(response)
        db.session.flush()

        for field in fields:
            value_text = None
            value_file = None

            if field.field_type == "file":
                file_obj = request.files.get(field.field_key)
                if file_obj and file_obj.filename:
                    try:
                        value_file = save_uploaded_file(file_obj, current_app.config["FORM_UPLOAD_DIR"], current_app.config["ALLOWED_DOC_EXTENSIONS"])
                    except ValueError:
                        value_file = None
            else:
                value_text = (request.form.get(field.field_key) or "").strip()

            item = FormResponseItem(response_id=response.id, field_id=field.id, value_text=value_text or None, value_file_path=value_file)
            db.session.add(item)

        token.is_used = True
        db.session.commit()
        return render_template("forms/public_done.html")

    return render_template("forms/public_fill.html", token=token, current_form=current_form, fields=fields)


@forms_bp.route("/<int:form_id>/responses")
@login_required
@role_required("ADMIN", "INFORMATICIEN", "EMPLOYEE")
def view_responses(form_id):
    current_form = DynamicForm.query.get_or_404(form_id)
    responses = FormResponse.query.filter_by(form_id=form_id).order_by(FormResponse.submitted_at.desc()).all()
    return render_template("forms/responses.html", current_form=current_form, responses=responses)

