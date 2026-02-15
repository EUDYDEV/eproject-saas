from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import BooleanField, DateField, EmailField, HiddenField, SelectField, StringField, SubmitField, TelField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class StudentForm(FlaskForm):
    branch_id = SelectField("Pays / Branche", coerce=int, validators=[Optional()])
    matricule = StringField("Matricule (auto)", validators=[Optional(), Length(max=50)])
    nom = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    prenoms = StringField("Prenoms", validators=[DataRequired(), Length(max=160)])
    sexe = SelectField("Sexe", choices=[("M", "M"), ("F", "F")], validators=[DataRequired()])
    date_naissance = DateField("Date naissance", validators=[Optional()])
    email = EmailField("Email", validators=[Optional()])
    procedure_email = EmailField("Email procedure", validators=[Optional()])
    procedure_email_password = StringField("Mot de passe email procedure", validators=[Optional(), Length(max=255)])
    telephone = TelField("Telephone", validators=[Optional(), Length(max=40)])
    adresse = TextAreaField("Adresse", validators=[Optional()])
    filiere = StringField("Filiere", validators=[DataRequired(), Length(max=120)])
    niveau = StringField("Niveau", validators=[DataRequired(), Length(max=80)])
    promotion = StringField("Promotion", validators=[DataRequired(), Length(max=20)])
    statut = SelectField("Statut", choices=[("actif", "Actif"), ("suspendu", "Suspendu"), ("ancien", "Ancien")], validators=[DataRequired()])
    photo = FileField("Photo", validators=[Optional(), FileAllowed(["jpg", "jpeg", "png", "webp"], "Image invalide")])
    submit = SubmitField("Enregistrer")


class GuardianForm(FlaskForm):
    nom = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    prenoms = StringField("Prenoms", validators=[DataRequired(), Length(max=160)])
    lien_parente = StringField("Lien parente", validators=[DataRequired(), Length(max=80)])
    telephone = TelField("Telephone", validators=[Optional(), Length(max=40)])
    email = EmailField("Email", validators=[Optional()])
    adresse = TextAreaField("Adresse", validators=[Optional()])
    contact_urgence = SelectField("Contact urgence", choices=[("0", "Non"), ("1", "Oui")], validators=[DataRequired()])
    submit = SubmitField("Enregistrer")


class StudentDocumentForm(FlaskForm):
    document_type = SelectField(
        "Type de document",
        choices=[
            ("lettre_motivation", "Lettre de motivation"),
            ("cni", "CNI"),
            ("passeport", "Passeport"),
            ("visa", "Visa"),
            ("autre", "Autre"),
        ],
        validators=[DataRequired()],
    )
    file = FileField(
        "Fichier",
        validators=[DataRequired(), FileAllowed(["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"], "Format non autorise")],
    )
    folder = HiddenField("Dossier", validators=[DataRequired(), Length(max=40)])
    submit = SubmitField("Uploader")


class StudentFolderCreateForm(FlaskForm):
    folder_name = StringField("Nouveau dossier", validators=[DataRequired(), Length(max=40)])
    submit = SubmitField("Creer le dossier")


class StudentCVForm(FlaskForm):
    profile_text = TextAreaField("Profil", validators=[Optional()])
    contact_details = TextAreaField("Coordonnees (en-tete, infos libres)", validators=[Optional()])

    show_hobbies = BooleanField("Afficher Centres d'interet")
    hobbies = TextAreaField("Centres d'interet (1 ligne = 1 element)", validators=[Optional()])

    show_languages = BooleanField("Afficher Langues")
    languages = TextAreaField("Langues (1 ligne = 1 element)", validators=[Optional()])

    show_skills = BooleanField("Afficher Expertise")
    skills = TextAreaField("Expertise (1 ligne = 1 element)", validators=[Optional()])

    show_education = BooleanField("Afficher Education")
    education = TextAreaField("Education / diplomes", validators=[Optional()])
    show_social_links = BooleanField("Afficher Suivez-moi")
    social_links = TextAreaField("Suivez-moi (1 ligne = 1 element)", validators=[Optional()])

    show_professional_experience = BooleanField("Afficher Experiences professionnelles")
    professional_experience = TextAreaField("Experiences professionnelles", validators=[Optional()])

    show_extra_experience = BooleanField("Afficher Experiences extra-professionnelles")
    extra_experience = TextAreaField("Experiences extra-professionnelles", validators=[Optional()])

    show_software = BooleanField("Afficher Logiciels")
    software = TextAreaField("Logiciels maitrises", validators=[Optional()])

    submit = SubmitField("Enregistrer CV")
