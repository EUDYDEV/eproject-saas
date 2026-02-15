from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional


class StudentLoginForm(FlaskForm):
    matricule = StringField("Matricule", validators=[DataRequired(), Length(max=50)])
    password = PasswordField("Mot de passe", validators=[Optional(), Length(min=8, max=128)])
    submit = SubmitField("Se connecter")


class StudentChangePasswordForm(FlaskForm):
    new_password = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirmer le mot de passe",
        validators=[DataRequired(), EqualTo("new_password", message="Les mots de passe ne correspondent pas")],
    )
    submit = SubmitField("Changer le mot de passe")


class StudentPortalDocumentForm(FlaskForm):
    doc_type = StringField("Type de document", validators=[DataRequired(), Length(max=80)])
    notes = TextAreaField("Notes", validators=[Optional()])
    file = FileField("Fichier", validators=[DataRequired(), FileAllowed(["pdf", "jpg", "jpeg", "png", "docx"], "Format non autorise")])
    submit = SubmitField("Uploader")


class StudentProfileForm(FlaskForm):
    nom = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    prenoms = StringField("Prenoms", validators=[DataRequired(), Length(max=160)])
    email = StringField("Email", validators=[Optional(), Email(), Length(max=120)])
    telephone = StringField("Telephone", validators=[Optional(), Length(max=40)])
    adresse = TextAreaField("Adresse", validators=[Optional(), Length(max=2000)])
    avatar = FileField("Photo de profil", validators=[Optional(), FileAllowed(["png", "jpg", "jpeg", "webp"], "Image invalide")])
    submit = SubmitField("Mettre a jour")
