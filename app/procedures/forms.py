from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class EntityForm(FlaskForm):
    name = StringField("Nom entite", validators=[DataRequired(), Length(max=120)])
    is_partner = BooleanField("Partenaire")
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Enregistrer")


class SchoolForm(FlaskForm):
    entity_id = SelectField("Entite", coerce=int, validators=[DataRequired()])
    name = StringField("Nom ecole", validators=[DataRequired(), Length(max=255)])
    country = StringField("Pays", validators=[Optional(), Length(max=120)])
    city = StringField("Ville", validators=[Optional(), Length(max=120)])
    website = StringField("Site web", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Enregistrer")


class StudyCaseForm(FlaskForm):
    student_id = SelectField("Etudiant", coerce=int, validators=[DataRequired()])
    destination_country = StringField("Pays destination", validators=[Optional(), Length(max=120)])
    destination_city = StringField("Ville destination", validators=[Optional(), Length(max=120)])
    entity_id = SelectField("Entite", coerce=int, validators=[Optional()])
    school_id = SelectField("Ecole", coerce=int, validators=[Optional()])
    status = SelectField(
        "Statut",
        choices=[
            ("nouveau", "Nouveau"),
            ("dossier_en_cours", "Dossier en cours"),
            ("admission", "Admission"),
            ("visa", "Visa"),
            ("billet", "Billet"),
            ("arrive", "Arrive"),
            ("installe", "Installe"),
            ("abandonne", "Abandonne"),
        ],
        validators=[DataRequired()],
    )
    start_date = DateField("Date debut", validators=[Optional()])
    expected_departure_date = DateField("Depart prevu", validators=[Optional()])
    actual_departure_date = DateField("Depart reel", validators=[Optional()])
    arrival_date = DateField("Arrivee", validators=[Optional()])
    is_active = BooleanField("Dossier actif", default=True)
    submit = SubmitField("Enregistrer")


class CaseStageForm(FlaskForm):
    name = StringField("Nom etape", validators=[DataRequired(), Length(max=120)])
    status = SelectField(
        "Statut",
        choices=[("todo", "A faire"), ("doing", "En cours"), ("done", "Termine")],
        validators=[DataRequired()],
    )
    due_date = DateField("Echeance", validators=[Optional()])
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Ajouter")


class StageStatusForm(FlaskForm):
    status = SelectField(
        "Statut",
        choices=[("todo", "A faire"), ("doing", "En cours"), ("done", "Termine")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Mettre a jour")


class CaseDocumentForm(FlaskForm):
    doc_type = StringField("Type de document", validators=[DataRequired(), Length(max=80)])
    notes = TextAreaField("Notes", validators=[Optional()])
    file = FileField(
        "Fichier",
        validators=[DataRequired(), FileAllowed(["pdf", "jpg", "jpeg", "png", "docx"], "Format non autorise")],
    )
    submit = SubmitField("Uploader")


class CaseDocumentStatusForm(FlaskForm):
    review_status = SelectField(
        "Statut",
        choices=[("recu", "Recu"), ("valide", "Valide"), ("rejete", "Rejete")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Mettre a jour")


class CasePaymentForm(FlaskForm):
    label = StringField("Libelle", validators=[DataRequired(), Length(max=255)])
    amount = StringField("Montant", validators=[DataRequired(), Length(max=40)])
    currency = StringField("Devise", validators=[DataRequired(), Length(max=10)])
    paid = BooleanField("Etudiant a paye")
    paid_at = DateField("Date paiement", validators=[Optional()])
    submit = SubmitField("Enregistrer paiement")
