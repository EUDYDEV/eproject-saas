from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional


class LoginForm(FlaskForm):
    username = StringField("Nom d'utilisateur", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Mot de passe", validators=[DataRequired(), Length(min=6, max=128)])
    submit = SubmitField("Se connecter")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Mot de passe actuel", validators=[DataRequired(), Length(min=6, max=128)])
    new_password = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirmer le mot de passe",
        validators=[DataRequired(), EqualTo("new_password", message="Les mots de passe ne correspondent pas")],
    )
    submit = SubmitField("Changer le mot de passe")


class ProfileForm(FlaskForm):
    username = StringField("Nom d'utilisateur", validators=[DataRequired(), Length(min=3, max=80)])
    display_name = StringField("Nom affiche", validators=[Optional(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    phone = StringField("Telephone", validators=[Optional(), Length(max=40)])
    email_signature = TextAreaField("Signature email", validators=[Optional(), Length(max=2000)])
    avatar = FileField("Photo de profil", validators=[FileAllowed(["png", "jpg", "jpeg", "webp"], "Image invalide")])
    current_password = PasswordField("Mot de passe actuel", validators=[Optional(), Length(min=6, max=128)])
    new_password = PasswordField("Nouveau mot de passe", validators=[Optional(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirmer le mot de passe",
        validators=[Optional(), EqualTo("new_password", message="Les mots de passe ne correspondent pas")],
    )
    submit = SubmitField("Enregistrer mon profil")


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email du compte", validators=[DataRequired(), Email(), Length(max=120)])
    submit = SubmitField("Envoyer le lien de reinitialisation")


class ResetPasswordForm(FlaskForm):
    new_password = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirmer le mot de passe",
        validators=[DataRequired(), EqualTo("new_password", message="Les mots de passe ne correspondent pas")],
    )
    submit = SubmitField("Reinitialiser le mot de passe")


class AgencySignupForm(FlaskForm):
    agency_name = StringField("Nom de l'agence", validators=[DataRequired(), Length(min=2, max=120)])
    country_code = StringField("Code pays (ex: CI)", validators=[DataRequired(), Length(min=2, max=10)])
    city = StringField("Ville", validators=[Optional(), Length(max=120)])
    founder_name = StringField("Nom du dirigeant", validators=[DataRequired(), Length(min=3, max=80)])
    founder_email = StringField("Email professionnel", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Mot de passe", validators=[DataRequired(), Length(min=8, max=128)])
    plan_code = SelectField(
        "Plan d'abonnement",
        choices=[("starter", "Starter"), ("pro", "Pro"), ("enterprise", "Enterprise")],
        validators=[DataRequired()],
        default="starter",
    )
    submit = SubmitField("Creer mon agence")
