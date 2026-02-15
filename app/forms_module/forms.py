from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class DynamicFormBuilderForm(FlaskForm):
    title = StringField("Titre", validators=[DataRequired(), Length(max=255)])
    description = TextAreaField("Description", validators=[Optional()])
    submit = SubmitField("Creer")


class DynamicFieldForm(FlaskForm):
    field_key = StringField("Cle", validators=[DataRequired(), Length(max=80)])
    label = StringField("Label", validators=[DataRequired(), Length(max=255)])
    field_type = SelectField(
        "Type",
        choices=[("text", "Texte"), ("email", "Email"), ("tel", "Tel"), ("date", "Date"), ("select", "Liste"), ("comment", "Commentaire"), ("file", "Fichier")],
        validators=[DataRequired()],
    )
    is_required = SelectField("Obligatoire", choices=[("0", "Non"), ("1", "Oui")], validators=[DataRequired()])
    options_json = TextAreaField("Options (CSV pour type liste)", validators=[Optional()])
    sort_order = IntegerField("Ordre", validators=[DataRequired(), NumberRange(min=0)], default=0)
    submit = SubmitField("Ajouter champ")


class TokenGenerationForm(FlaskForm):
    form_id = SelectField("Formulaire", coerce=int, validators=[DataRequired()])
    email = StringField("Email destinataire", validators=[DataRequired(), Length(max=255)])
    student_id = IntegerField("Student ID", validators=[Optional()])
    guardian_id = IntegerField("Guardian ID", validators=[Optional()])
    expires_days = IntegerField("Expiration (jours)", validators=[DataRequired(), NumberRange(min=1, max=30)], default=7)
    submit = SubmitField("Generer lien")
