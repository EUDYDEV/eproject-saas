from flask_wtf import FlaskForm
from wtforms import DateField, FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class ArrivalSupportForm(FlaskForm):
    case_id = SelectField("Dossier", coerce=int, validators=[DataRequired()])
    host_entity_name = StringField("Entite d'accueil", validators=[Optional(), Length(max=255)])
    contact_name = StringField("Contact", validators=[Optional(), Length(max=255)])
    phone = StringField("Telephone", validators=[Optional(), Length(max=80)])
    email = StringField("Email", validators=[Optional(), Length(max=120)])
    lodging_status = SelectField(
        "Logement",
        choices=[("", "-"), ("prevu", "Prevu"), ("ok", "OK"), ("probleme", "Probleme")],
        validators=[Optional()],
    )
    pickup_status = SelectField(
        "Pickup",
        choices=[("", "-"), ("prevu", "Prevu"), ("ok", "OK"), ("probleme", "Probleme")],
        validators=[Optional()],
    )
    mentor_assigned = StringField("Mentor assigne", validators=[Optional(), Length(max=255)])
    followup_notes = TextAreaField("Notes de suivi", validators=[Optional()])
    confirmed_at = DateField("Date confirmation", validators=[Optional()])
    submit = SubmitField("Enregistrer")


class CommissionRuleForm(FlaskForm):
    entity_id = SelectField("Entite", coerce=int, validators=[DataRequired()])
    school_id = SelectField("Ecole (optionnel)", coerce=int, validators=[Optional()])
    amount_per_student = FloatField("Montant / etudiant", validators=[DataRequired(), NumberRange(min=0)])
    currency = StringField("Devise", validators=[DataRequired(), Length(max=10)], default="EUR")
    trigger_status = SelectField(
        "Declencheur",
        choices=[("arrive", "Arrive"), ("installe", "Installe")],
        validators=[DataRequired()],
        default="installe",
    )
    submit = SubmitField("Ajouter regle")


class CommissionPayForm(FlaskForm):
    paid_at = DateField("Date paiement", validators=[Optional()])
    submit = SubmitField("Marquer payee")
