from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class SMTPForm(FlaskForm):
    host = StringField("Host", validators=[DataRequired(), Length(max=255)])
    port = IntegerField("Port", validators=[DataRequired()])
    username = StringField("Username", validators=[DataRequired(), Length(max=255)])
    password = StringField("Password", validators=[DataRequired(), Length(max=255)])
    from_email = StringField("From Email", validators=[DataRequired(), Email(), Length(max=255)])
    use_tls = BooleanField("TLS", default=True)
    submit = SubmitField("Sauvegarder")


class EmailTemplateForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    subject = StringField("Sujet", validators=[DataRequired(), Length(max=255)])
    body_html = TextAreaField("HTML", validators=[DataRequired()])
    body_text = TextAreaField("Texte", validators=[Optional()])
    submit = SubmitField("Sauvegarder")


class EmailSendForm(FlaskForm):
    target = SelectField(
        "Cible",
        choices=[
            ("students", "Etudiants"),
            ("guardians", "Parents"),
            ("abroad_students", "Etudiants a l'etranger"),
            ("mix", "Etudiants/Parents"),
        ],
        validators=[DataRequired()],
    )
    branch_id = SelectField("Branche", coerce=int, validators=[Optional()])
    cta_label = StringField("Libelle bouton RDV", validators=[Optional(), Length(max=120)])
    submit = SubmitField("Envoyer")


class DirectEmailForm(FlaskForm):
    target = SelectField(
        "Cible",
        choices=[
            ("students", "Etudiants"),
            ("guardians", "Parents"),
            ("abroad_students", "Etudiants a l'etranger"),
            ("mix", "Etudiants/Parents"),
        ],
        validators=[DataRequired()],
    )
    branch_id = SelectField("Branche", coerce=int, validators=[Optional()])
    cta_label = StringField("Libelle bouton RDV", validators=[Optional(), Length(max=120)])
    subject = StringField("Sujet", validators=[DataRequired(), Length(max=255)])
    body_html = TextAreaField("Votre message", validators=[DataRequired()])
    body_text = TextAreaField("Message texte (optionnel)", validators=[Optional()])
    submit = SubmitField("Envoyer maintenant")


class OrientationInviteForm(FlaskForm):
    target = SelectField(
        "Cible",
        choices=[
            ("students", "Etudiants"),
            ("guardians", "Parents"),
            ("abroad_students", "Etudiants a l'etranger"),
            ("mix", "Etudiants/Parents"),
        ],
        validators=[DataRequired()],
        default="students",
    )
    branch_id = SelectField("Branche", coerce=int, validators=[Optional()])
    subject = StringField("Sujet", validators=[DataRequired(), Length(max=255)])
    intro_message = TextAreaField("Message d'introduction (optionnel)", validators=[Optional()])
    event_title = StringField("Evenement", validators=[Optional(), Length(max=255)])
    event_date = StringField("Date", validators=[Optional(), Length(max=120)])
    event_address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    event_phone = StringField("Contacts", validators=[Optional(), Length(max=120)])
    event_email = StringField("Email", validators=[Optional(), Email(), Length(max=255)])
    representative_name = StringField("Representant", validators=[Optional(), Length(max=255)])
    cta_label = StringField("Libelle bouton", validators=[DataRequired(), Length(max=120)], default="Confirmer mon RDV")
    submit = SubmitField("Envoyer invitation orientation")
