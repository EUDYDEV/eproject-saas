from flask_wtf import FlaskForm
from wtforms import DateField, DateTimeLocalField, IntegerField, SelectField, StringField, SubmitField, TextAreaField, TimeField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional


class AppointmentForm(FlaskForm):
    motif = StringField("Motif", validators=[DataRequired(), Length(max=255)])
    responder_name = StringField("Nom complet", validators=[DataRequired(), Length(max=255)])
    responder_email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    responder_phone = StringField("Telephone", validators=[DataRequired(), Length(max=80)])
    requested_date = DateField("Date souhaitee", validators=[DataRequired()])
    requested_slot = StringField("Creneau", validators=[DataRequired(), Length(max=80)])
    commentaire = TextAreaField("Commentaire", validators=[Optional()])
    submit = SubmitField("Confirmer mon RDV")


class AppointmentDecisionForm(FlaskForm):
    status = SelectField("Decision", choices=[("accepted", "Accepter"), ("refused", "Refuser")], validators=[DataRequired()])
    admin_comment = TextAreaField("Commentaire admin", validators=[Optional()])
    submit = SubmitField("Valider")


class EventSettingsForm(FlaskForm):
    event_title = StringField("Nom de l'evenement", validators=[Optional(), Length(max=255)])
    orientation_date = StringField("Date", validators=[Optional(), Length(max=120)])
    orientation_address = StringField("Adresse", validators=[Optional(), Length(max=255)])
    orientation_phone = StringField("Telephone", validators=[Optional(), Length(max=80)])
    representative_name = StringField("Notre representant(e)", validators=[Optional(), Length(max=255)])
    appointment_slots = TextAreaField("Creneaux (1 ligne = 1 horaire)", validators=[Optional()])
    max_appointments_per_day = IntegerField("Max RDV par jour", validators=[DataRequired(), NumberRange(min=1, max=100)], default=10)
    submit = SubmitField("Sauvegarder les infos")


class EventForm(FlaskForm):
    title = StringField("Titre", validators=[DataRequired(), Length(max=255)])
    slug = StringField("Slug public", validators=[DataRequired(), Length(max=255)])
    description_html = TextAreaField("Description", validators=[Optional()])
    location = StringField("Lieu", validators=[Optional(), Length(max=255)])
    timezone = StringField("Timezone", validators=[Optional(), Length(max=80)])
    start_date = DateField("Date debut", validators=[DataRequired()])
    end_date = DateField("Date fin", validators=[DataRequired()])
    day_start_time = TimeField("Debut journee", validators=[DataRequired()])
    day_end_time = TimeField("Fin journee", validators=[DataRequired()])
    slot_minutes = IntegerField("Duree creneau (minutes)", validators=[DataRequired(), NumberRange(min=10, max=240)], default=30)
    max_per_day = IntegerField("Max RDV / jour", validators=[DataRequired(), NumberRange(min=1, max=100)], default=10)
    is_active = SelectField("Actif", choices=[("1", "Oui"), ("0", "Non")], validators=[DataRequired()], default="1")
    submit = SubmitField("Enregistrer")


class EventTokenForm(FlaskForm):
    student_id = SelectField("Etudiant", coerce=int, validators=[Optional()])
    expires_at = DateTimeLocalField("Expire le", format="%Y-%m-%dT%H:%M", validators=[DataRequired()])
    submit = SubmitField("Generer lien token")


class SlotBookingForm(FlaskForm):
    name = StringField("Nom complet", validators=[DataRequired(), Length(max=255)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    phone = StringField("Telephone", validators=[Optional(), Length(max=80)])
    slot_id = SelectField("Creneau disponible", coerce=int, validators=[DataRequired()])
    submit = SubmitField("Confirmer mon RDV")
