from urllib.parse import urlparse

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, ValidationError


def _optional_https_url(form, field):
    value = (field.data or "").strip()
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValidationError("URL invalide: utilisez un lien complet en https://")


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    role = SelectField(
        "Role",
        choices=[
            ("FOUNDER", "FOUNDER"),
            ("ADMIN_BRANCH", "ADMIN_BRANCH"),
            ("EMPLOYEE", "EMPLOYEE"),
            ("IT", "IT"),
        ],
        validators=[DataRequired()],
    )
    branch_id = SelectField("Branche", coerce=int, validators=[Optional()])
    password = PasswordField("Mot de passe", validators=[Optional(), Length(min=6, max=128)])
    is_active = BooleanField("Actif", default=True)
    must_change_password = BooleanField("Forcer changement mot de passe", default=True)
    submit = SubmitField("Enregistrer")


class BranchForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    logo_file = FileField("Logo agence (fichier)", validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp"], "Format image invalide"), FileSize(max_size=5 * 1024 * 1024)])
    country_code = StringField("Code pays", validators=[DataRequired(), Length(max=10)])
    city = StringField("Ville", validators=[Optional(), Length(max=120)])
    address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    phone = StringField("Telephone", validators=[Optional(), Length(max=80)])
    email = StringField("Email", validators=[Optional(), Email(), Length(max=120)])
    website_url = StringField("Site web", validators=[Optional(), Length(max=255), _optional_https_url])
    timezone = StringField("Timezone", validators=[Optional(), Length(max=80)])
    submit = SubmitField("Enregistrer")


class PlatformSettingsForm(FlaskForm):
    site_name = StringField("Nom du site", validators=[DataRequired(), Length(max=120)])
    site_logo_file = FileField("Logo plateforme (fichier)", validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp"], "Format image invalide"), FileSize(max_size=5 * 1024 * 1024)])
    site_tagline = StringField("Slogan", validators=[Optional(), Length(max=255)])
    site_footer_text = StringField("Texte footer", validators=[Optional(), Length(max=255)])
    site_logo_url = StringField("Logo URL", validators=[Optional(), Length(max=255), _optional_https_url])
    plan_starter_price = StringField("Prix plan Starter", validators=[Optional(), Length(max=30)])
    plan_pro_price = StringField("Prix plan Pro", validators=[Optional(), Length(max=30)])
    plan_enterprise_price = StringField("Prix plan Enterprise", validators=[Optional(), Length(max=30)])
    plan_currency = StringField("Devise abonnement", validators=[Optional(), Length(max=10)])
    payment_link = StringField("Lien de paiement", validators=[Optional(), Length(max=500), _optional_https_url])
    payment_link_starter = StringField("Lien paiement Starter", validators=[Optional(), Length(max=500), _optional_https_url])
    payment_link_pro = StringField("Lien paiement Pro", validators=[Optional(), Length(max=500), _optional_https_url])
    payment_link_enterprise = StringField("Lien paiement Enterprise", validators=[Optional(), Length(max=500), _optional_https_url])
    billing_sender_email = StringField("Email plateforme IT", validators=[Optional(), Email(), Length(max=255)])
    expiry_notice_days = StringField("Alerte expiration (jours)", validators=[Optional(), Length(max=5)])
    submit = SubmitField("Sauvegarder")


class ResetUserPasswordForm(FlaskForm):
    user_id = SelectField("Utilisateur", coerce=int, validators=[DataRequired()])
    new_password = PasswordField("Nouveau mot de passe (optionnel)", validators=[Optional(), Length(min=8, max=128)])
    force_change = BooleanField("Forcer changement au prochain login", default=True)
    submit_user = SubmitField("Reinitialiser utilisateur")


class ResetStudentPasswordForm(FlaskForm):
    student_id = SelectField("Etudiant", coerce=int, validators=[DataRequired()])
    new_password = PasswordField("Nouveau mot de passe (optionnel)", validators=[Optional(), Length(min=8, max=128)])
    force_change = BooleanField("Forcer changement au prochain login", default=True)
    submit_student = SubmitField("Reinitialiser etudiant")


class UserEmailForm(FlaskForm):
    subject = StringField("Sujet", validators=[DataRequired(), Length(max=255)])
    body = TextAreaField("Message", validators=[DataRequired(), Length(max=10000)])
    submit = SubmitField("Envoyer email")


class ITClientEmailForm(FlaskForm):
    subscription_status = SelectField(
        "Statut abonnement",
        choices=[
            ("all", "Toutes"),
            ("active", "Actives"),
            ("pending_review", "En validation"),
            ("pending", "En attente"),
            ("expired", "Expirees"),
        ],
        validators=[DataRequired()],
        default="all",
    )
    branch_id = SelectField("Agence", coerce=int, validators=[Optional()], default=0)
    subject = StringField("Sujet", validators=[DataRequired(), Length(max=255)])
    body = TextAreaField("Message", validators=[DataRequired(), Length(max=10000)])
    submit = SubmitField("Envoyer aux clients")
