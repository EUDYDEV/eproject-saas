import os
from datetime import datetime
from types import SimpleNamespace

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.auth.forms import AgencySignupForm, ChangePasswordForm, ForgotPasswordForm, LoginForm, ProfileForm, ResetPasswordForm
from app.extensions import db, limiter
from app.models import AgencySubscription, Branch, EmailLog, Membership, SMTPSetting, User
from app.utils.audit import add_audit_log
from app.utils.authz import is_super_admin_platform, normalized_role
from app.utils.emailer import send_email_smtp
from app.utils.files import save_uploaded_file
from app.utils.subscriptions import (
    get_or_create_portal_settings,
    get_subscription_for_user,
    price_for_plan,
    subscriptions_enforced,
    user_has_active_subscription,
    send_subscription_transactional_email,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
password_hasher = PasswordHasher()


def is_subscription_owner(user):
    if not user:
        return False
    return AgencySubscription.query.filter_by(owner_user_id=user.id).first() is not None


def email_needs_update(email_value):
    email = (email_value or "").strip().lower()
    if not email or "@" not in email:
        return True
    domain = email.split("@", 1)[1]
    if domain in {"innovformation", "example.com", "localhost", "local"}:
        return True
    if "." not in domain:
        return True
    return False



def _slugify(value):
    raw = (value or "").strip().lower()
    out = []
    last_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        else:
            if not last_dash:
                out.append("-")
                last_dash = True
    slug = "".join(out).strip("-")
    return slug or "agence"


def _unique_branch_slug(name):
    base = _slugify(name)
    slug = base
    idx = 2
    while Branch.query.filter_by(slug=slug).first() is not None:
        slug = f"{base}-{idx}"
        idx += 1
    return slug
def _password_reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _effective_smtp_settings(branch_id=None):
    if branch_id:
        settings = SMTPSetting.query.filter_by(branch_id=branch_id).first()
        if settings:
            return settings

    settings = SMTPSetting.query.filter_by(branch_id=None).first()
    if settings:
        return settings

    cfg = current_app.config
    required = [cfg.get("SMTP_HOST"), cfg.get("SMTP_USERNAME"), cfg.get("SMTP_PASSWORD"), cfg.get("SMTP_FROM")]
    if all(required):
        return SimpleNamespace(
            host=cfg["SMTP_HOST"],
            port=cfg["SMTP_PORT"],
            username=cfg["SMTP_USERNAME"],
            password=cfg["SMTP_PASSWORD"],
            from_email=cfg["SMTP_FROM"],
            use_tls=cfg["SMTP_TLS"],
        )
    return None


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    if current_user.is_authenticated:
        if current_user.must_change_password and request.endpoint != "auth.change_password":
            return redirect(url_for("auth.change_password"))
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user and user.is_active:
            try:
                if password_hasher.verify(user.password_hash, form.password.data):
                    sub = get_subscription_for_user(user) if user.role in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE") else None
                    if sub is not None and sub.status == "expired":
                        if user.role == "FOUNDER" or is_subscription_owner(user):
                            login_user(user)
                            add_audit_log(user.id, "login", "Connexion utilisateur (abonnement expire)", branch_id=user.branch_id, action="login")
                            flash("Votre abonnement est expiré. Merci de choisir un plan pour reactiver votre agence.", "warning")
                            return redirect(url_for("auth.subscription_status"))
                        flash("Compte agence expire: contactez votre propriétaire pour réabonnément.", "warning")
                        return render_template("auth/login.html", form=form)

                    if (
                        subscriptions_enforced()
                        and user.role in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE")
                        and not user_has_active_subscription(user)
                    ):
                        if user.role == "FOUNDER" or is_subscription_owner(user):
                            login_user(user)
                            add_audit_log(user.id, "login", "Connexion utilisateur", branch_id=user.branch_id, action="login")
                            flash("Abonnement agence inactif ou expire. Merci de payer pour debloquer toute l'équipe.", "warning")
                            return redirect(url_for("auth.subscription_status"))
                        flash("Compte agence expire: contactez votre propriétaire pour réabonnément.", "warning")
                        return render_template("auth/login.html", form=form)

                    login_user(user)
                    add_audit_log(user.id, "login", "Connexion utilisateur", branch_id=user.branch_id, action="login")
                    if user.must_change_password:
                        flash("Vous devez changer votre mot de passe avant de continuer.", "warning")
                        return redirect(url_for("auth.change_password"))
                    if email_needs_update(user.email):
                        flash("Renseigne ton email reel dans Mon profil pour continuer.", "warning")
                        return redirect(url_for("auth.profile"))
                    return redirect(url_for("dashboard.index"))
            except VerifyMismatchError:
                pass

        flash("Identifiants invalides.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/signup-agency", methods=["POST"])
@limiter.limit("3 per minute")
def signup_agency():
    form = AgencySignupForm()
    settings = get_or_create_portal_settings()
    if not form.validate_on_submit():
        for field, errs in form.errors.items():
            if errs:
                flash(f"{field}: {errs[0]}", "danger")
        return redirect(url_for("dashboard.index"))

    founder_email = form.founder_email.data.strip().lower()
    founder_username = form.founder_name.data.strip()
    agency_name = form.agency_name.data.strip()
    country_code = form.country_code.data.strip().upper()
    city = (form.city.data or "").strip() or None
    plan_code = form.plan_code.data

    existing_email = User.query.filter_by(email=founder_email).first()
    if existing_email:
        flash("Cet email existe deja. Connecte-toi directement.", "warning")
        return redirect(url_for("auth.login"))

    branch = Branch(
        name=agency_name,
        slug=_unique_branch_slug(agency_name),
        country_code=country_code,
        city=city,
        timezone="Africa/Abidjan",
    )
    db.session.add(branch)
    db.session.flush()

    owner = User(
        username=founder_username,
        email=founder_email,
        role="FOUNDER",
        branch_id=branch.id,
        password_hash=password_hasher.hash(form.password.data),
        is_active=True,
        must_change_password=False,
    )
    db.session.add(owner)
    db.session.flush()

    membership = Membership(user_id=owner.id, branch_id=branch.id, role="OWNER")
    db.session.add(membership)

    amount, currency = price_for_plan(settings, plan_code)
    sub_status = "pending"
    starts_at = None
    ends_at = None
    paid_at = None
    if not subscriptions_enforced(settings):
        sub_status = "active"
        starts_at = datetime.utcnow()
        paid_at = starts_at

    sub = AgencySubscription(
        branch_id=branch.id,
        owner_user_id=owner.id,
        plan_code=plan_code,
        amount=amount,
        currency=currency,
        status=sub_status,
        starts_at=starts_at,
        ends_at=ends_at,
        paid_at=paid_at,
    )
    db.session.add(sub)
    db.session.commit()

    add_audit_log(owner.id, "agency_signup", f"Nouvelle agence: {agency_name}", branch_id=branch.id, action="agency_signup")
    login_user(owner)
    if subscriptions_enforced(settings):
        flash("Compte agence créée. Termine le paiement pour activer le dashboard.", "info")
        return redirect(url_for("auth.subscription_status"))
    flash("Compte agence créée en mode gratuit.", "success")
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/subscription", methods=["GET", "POST"])
@login_required
def subscription_status():
    if current_user.role not in ("FOUNDER",) and not is_subscription_owner(current_user):
        return redirect(url_for("dashboard.index"))

    settings = get_or_create_portal_settings()
    sub = get_subscription_for_user(current_user)
    if sub is None:
        if not current_user.branch_id:
            owner_sub = AgencySubscription.query.filter_by(owner_user_id=current_user.id).first()
            if owner_sub and owner_sub.branch_id:
                current_user.branch_id = owner_sub.branch_id
                if Membership.query.filter_by(user_id=current_user.id, branch_id=owner_sub.branch_id).first() is None:
                    db.session.add(Membership(user_id=current_user.id, branch_id=owner_sub.branch_id, role="OWNER"))
                db.session.commit()
            else:
                auto_branch = Branch(
                    name=f"Agence {current_user.display_name or current_user.username}",
                    country_code="CI",
                    city="Abidjan",
                    timezone="Africa/Abidjan",
                )
                db.session.add(auto_branch)
                db.session.flush()
                current_user.branch_id = auto_branch.id
                if Membership.query.filter_by(user_id=current_user.id, branch_id=auto_branch.id).first() is None:
                    db.session.add(Membership(user_id=current_user.id, branch_id=auto_branch.id, role="OWNER"))
                db.session.commit()

        sub = get_subscription_for_user(current_user)
        if sub is None:
            amount, currency = price_for_plan(settings, "starter")
            sub_status = "pending"
            starts_at = None
            ends_at = None
            paid_at = None
            if not subscriptions_enforced(settings):
                sub_status = "active"
                starts_at = datetime.utcnow()
                paid_at = starts_at

            sub = AgencySubscription(
                branch_id=current_user.branch_id,
                owner_user_id=current_user.id,
                plan_code="starter",
                amount=amount,
                currency=currency,
                status=sub_status,
                starts_at=starts_at,
                ends_at=ends_at,
                paid_at=paid_at,
            )
            db.session.add(sub)
            db.session.commit()

    if not subscriptions_enforced(settings):
        if sub.status != "active":
            sub.status = "active"
            if not sub.starts_at:
                sub.starts_at = datetime.utcnow()
            if not sub.paid_at:
                sub.paid_at = datetime.utcnow()
            db.session.commit()
        flash("Mode gratuit actif (plans a 0). Aucun blocage d'abonnement applique.", "info")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "mark_payment_sent":
            sub.status = "pending_review"
            sub.payment_reference = (request.form.get("payment_reference") or "").strip() or None
            db.session.commit()

            ref_text = sub.payment_reference or "Non renseignee"
            subject = "Paiement recu - verification en cours"
            html_body = (
                "<p>Bonjour,</p>"
                "<p>Nous avons bien reçu votre déclaration de paiement d'abonnement.</p>"
                f"<p><strong>Référence :</strong> {ref_text}</p>"
                "<p>Felicitation et merci de vous etre réabonné. "
                "Votre compte sera actif dans 10 minutes apres verification par notre équipe.</p>"
                "<p>Cordialement,<br>Service facturation E-PROJECT</p>"
            )
            text_body = (
                "Bonjour,\n\n"
                "Nous avons bien reçu votre déclaration de paiement d'abonnement.\n"
                f"Référence : {ref_text}\n\n"
                "Felicitation et merci de vous etre réabonné. "
                "Votre compte sera actif dans 10 minutes apres verification par notre équipe.\n\n"
                "Service facturation E-PROJECT"
            )
            send_subscription_transactional_email(sub, subject, html_body, text_body, sent_by=None)

            flash("Félicitations, merci de vous être réabonné. Votre compte sera actif dans 10 minutes.", "subscription_popup")
            return redirect(url_for("auth.subscription_status"))
        if action == "change_plan":
            selected_plan = (request.form.get("plan_code") or "starter").strip().lower()
            amount, currency = price_for_plan(settings, selected_plan)
            sub.plan_code = selected_plan
            sub.amount = amount
            sub.currency = currency
            if sub.status != "active":
                sub.status = "pending"
            db.session.commit()
            flash("Plan mis a jour.", "success")
            return redirect(url_for("auth.subscription_status"))

    if sub.status == "active" and (sub.ends_at is None or sub.ends_at > datetime.utcnow()):
        return redirect(url_for("dashboard.index"))

    plans = [
        {"code": "starter", "name": "Starter", "price": settings.plan_starter_price or 0, "currency": settings.plan_currency or "XOF"},
        {"code": "pro", "name": "Pro", "price": settings.plan_pro_price or 0, "currency": settings.plan_currency or "XOF"},
        {"code": "enterprise", "name": "Enterprise", "price": settings.plan_enterprise_price or 0, "currency": settings.plan_currency or "XOF"},
    ]
    plan_payment_links = {
        "starter": settings.payment_link_starter or settings.payment_link,
        "pro": settings.payment_link_pro or settings.payment_link,
        "enterprise": settings.payment_link_enterprise or settings.payment_link,
    }
    current_plan_payment_link = plan_payment_links.get((sub.plan_code or "").lower(), settings.payment_link)
    return render_template(
        "auth/subscription.html",
        subscription=sub,
        settings=settings,
        plans=plans,
        missing_branch=False,
        plan_payment_links=plan_payment_links,
        current_plan_payment_link=current_plan_payment_link,
    )


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()
        smtp = _effective_smtp_settings(user.branch_id if user else None)
        sent = False

        if user and user.is_active and smtp:
            token = _password_reset_serializer().dumps({"uid": user.id})
            reset_link = url_for("auth.reset_password", token=token, _external=True)
            platform_name = (get_or_create_portal_settings().site_name or "E-PROJECT").strip()
            subject = f"Réinitialisation du mot de passe - {platform_name}"
            body_text = (
                "Bonjour,\n\n"
                "Vous avez demandé la réinitialisation de votre mot de passe.\n"
                f"Cliquez sur ce lien (valable 1 heure) : {reset_link}\n\n"
                "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."
            )
            body_html = (
                "<p>Bonjour,</p>"
                "<p>Vous avez demandé la réinitialisation de votre mot de passe.</p>"
                f"<p><a href='{reset_link}' style='background:#0d6efd;color:#fff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;'>Réinitialiser mon mot de passe</a></p>"
                "<p style='font-size:12px;color:#6b7280'>Lien valable 1 heure. Si ce n'est pas vous, ignorez cet e-mail.</p>"
            )
            try:
                send_email_smtp(smtp, user.email, subject, body_html, body_text)
                db.session.add(
                    EmailLog(
                        branch_id=user.branch_id,
                        to_email=user.email,
                        subject=subject,
                        status="sent",
                        sent_by=None,
                    )
                )
                db.session.commit()
                sent = True
            except Exception as exc:
                db.session.add(
                    EmailLog(
                        branch_id=user.branch_id,
                        to_email=user.email,
                        subject=subject,
                        status="failed",
                        error=str(exc),
                        sent_by=None,
                    )
                )
                db.session.commit()

        # Message neutre pour eviter la fuite d'info compte.
        flash("Si l'e-mail existe, un lien de réinitialisation a été envoyé.", "info")
        if user and sent:
            add_audit_log(user.id, "password_reset_request", "Demande mot de passe oublie", branch_id=user.branch_id, action="password_reset_request")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = ResetPasswordForm()
    try:
        payload = _password_reset_serializer().loads(token, max_age=3600)
        user_id = payload.get("uid")
    except SignatureExpired:
        flash("Lien expire. Demandez un nouveau lien.", "warning")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("Lien invalide.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.get(user_id)
    if not user or not user.is_active:
        flash("Compte introuvable ou inactif.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if form.validate_on_submit():
        user.password_hash = password_hasher.hash(form.new_password.data)
        user.must_change_password = False
        db.session.commit()
        add_audit_log(user.id, "password_reset_done", "Mot de passe réinitialisé via lien email", branch_id=user.branch_id, action="password_reset_done")
        flash("Mot de passe réinitialisé. Connecte-toi maintenant.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            if not password_hasher.verify(current_user.password_hash, form.current_password.data):
                flash("Mot de passe actuel invalide.", "danger")
                return render_template("auth/change_password.html", form=form)
        except VerifyMismatchError:
            flash("Mot de passe actuel invalide.", "danger")
            return render_template("auth/change_password.html", form=form)

        current_user.password_hash = password_hasher.hash(form.new_password.data)
        current_user.must_change_password = False
        db.session.commit()
        add_audit_log(current_user.id, "password_changed", "Changement mot de passe", branch_id=current_user.branch_id, action="password_change")
        flash("Mot de passe mis a jour.", "success")
        if email_needs_update(current_user.email):
            flash("Renseigne maintenant ton email reel dans Mon profil.", "warning")
            return redirect(url_for("auth.profile"))
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html", form=form)


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if request.method == "GET":
        form.username.data = current_user.username

    if form.validate_on_submit():
        username = form.username.data.strip()
        existing_user = User.query.filter(User.username == username, User.id != current_user.id).first()
        if existing_user:
            flash("Ce nom d'utilisateur est deja utilise.", "danger")
            return render_template("auth/profile.html", form=form)

        email = form.email.data.strip().lower()
        existing = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing:
            flash("Cet email est deja utilise par un autre compte.", "danger")
            return render_template("auth/profile.html", form=form)

        wants_password_change = bool((form.current_password.data or "").strip() or (form.new_password.data or "").strip() or (form.confirm_password.data or "").strip())
        if wants_password_change:
            if not (form.current_password.data and form.new_password.data and form.confirm_password.data):
                flash("Pour changer le mot de passe, remplis les 3 champs mot de passe.", "danger")
                return render_template("auth/profile.html", form=form)
            try:
                if not password_hasher.verify(current_user.password_hash, form.current_password.data):
                    flash("Mot de passe actuel invalide.", "danger")
                    return render_template("auth/profile.html", form=form)
            except VerifyMismatchError:
                flash("Mot de passe actuel invalide.", "danger")
                return render_template("auth/profile.html", form=form)
            current_user.password_hash = password_hasher.hash(form.new_password.data)
            current_user.must_change_password = False

        current_user.username = username
        current_user.display_name = (form.display_name.data or "").strip() or None
        current_user.email = email
        current_user.phone = (form.phone.data or "").strip() or None
        current_user.email_signature = (form.email_signature.data or "").strip() or None

        if form.avatar.data:
            upload_dir = os.path.join(current_app.static_folder, "uploads", "users", str(current_user.id))
            stored_name = save_uploaded_file(form.avatar.data, upload_dir, current_app.config["ALLOWED_IMAGE_EXTENSIONS"])
            current_user.avatar_path = f"uploads/users/{current_user.id}/{stored_name}"

        db.session.commit()
        add_audit_log(current_user.id, "profile_update", "Profil utilisateur mis a jour", branch_id=current_user.branch_id, action="profile_update")
        flash("Profil mis a jour.", "success")
        return redirect(url_for("auth.profile"))

    return render_template("auth/profile.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    add_audit_log(current_user.id, "logout", "Deconnexion utilisateur", branch_id=current_user.branch_id, action="logout")
    logout_user()
    flash("Session fermee.", "success")
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/it/scope", methods=["POST"])
@login_required
def set_it_scope():
    if not is_super_admin_platform(current_user):
        return redirect(url_for("dashboard.index"))

    raw_scope = (request.form.get("scope_branch_id") or "0").strip()
    try:
        scope_id = int(raw_scope)
    except ValueError:
        scope_id = 0

    if scope_id <= 0:
        session.pop("it_scope_branch_id", None)
        flash("Mode IT global active.", "info")
        if request.form.get("go_agency") == "1":
            return redirect(url_for("dashboard.it_saas_dashboard"))
        return redirect(request.referrer or url_for("dashboard.index"))

    branch = Branch.query.get(scope_id)
    if not branch:
        flash("Branche invalide.", "danger")
        return redirect(request.referrer or url_for("dashboard.index"))

    session["it_scope_branch_id"] = branch.id
    flash(f"Mode IT agence active: {branch.name}.", "success")
    if request.form.get("go_agency") == "1":
        return redirect(url_for("dashboard.index", agency_view=1, branch_id=branch.id))
    return redirect(request.referrer or url_for("dashboard.index"))



