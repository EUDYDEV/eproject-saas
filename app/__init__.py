from argon2 import PasswordHasher
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
from flask import Flask, Response, flash, redirect, request, session, url_for
from flask_login import current_user, logout_user
from sqlalchemy.exc import OperationalError, ProgrammingError
from werkzeug.middleware.proxy_fix import ProxyFix

from app.admin.routes import admin_bp
from app.appointments.routes import appointments_bp, public_rdv_bp
from app.auth.routes import auth_bp, email_needs_update
from app.config import Config
from app.dashboard.routes import dashboard_bp
from app.emails.routes import emails_bp
from app.extensions import csrf, db, limiter, login_manager, migrate
from app.forms_module.routes import forms_bp
from app.logs.routes import logs_bp
from app.models import AgencySubscription, AuditLog, Branch, Entity, Membership, PortalSetting, Student, StudentAuth, User
from app.procedures.routes import procedures_bp
from app.student_portal.routes import student_portal_bp
from app.students.routes import students_bp
from app.utils.authz import is_super_admin_platform, normalized_role
from app.utils.subscriptions import (
    get_or_create_portal_settings,
    get_subscription_for_user,
    is_subscription_active_for_user,
    process_subscription_notifications,
    subscriptions_enforced,
)


password_hasher = PasswordHasher()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(students_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(emails_bp)
    app.register_blueprint(forms_bp)
    app.register_blueprint(appointments_bp)
    app.register_blueprint(public_rdv_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(procedures_bp)
    app.register_blueprint(student_portal_bp)

    register_cli(app)
    ensure_runtime_schema_compat(app)
    ensure_runtime_tables(app)
    ensure_platform_it_account(app)

    @app.route("/health")
    def healthcheck():
        return {"status": "ok"}

    @app.route("/login")
    def login_alias():
        return redirect(url_for("auth.login"))

    @app.route("/robots.txt")
    def robots_txt():
        base_url = (app.config.get("PUBLIC_BASE_URL") or "").strip()
        if not base_url:
            base_url = request.url_root.rstrip("/")
        lines = [
            "User-agent: *",
            "Allow: /",
            "Disallow: /auth/",
            "Disallow: /student/",
            "Disallow: /admin/",
            "Sitemap: " + base_url + url_for("sitemap_xml"),
        ]
        return Response("\n".join(lines), mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        pages = [
            url_for("dashboard.index", _external=True),
            url_for("auth.login", _external=True),
            url_for("auth.forgot_password", _external=True),
            url_for("student_portal.login", _external=True),
        ]
        today = datetime.utcnow().date().isoformat()
        xml_items = []
        for loc in sorted(set(pages)):
            xml_items.append(
                f"<url><loc>{xml_escape(loc)}</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>"
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(xml_items)
            + "</urlset>"
        )
        return Response(xml, mimetype="application/xml")

    @app.context_processor
    def inject_globals():
        try:
            settings = get_or_create_portal_settings()
        except (OperationalError, ProgrammingError):
            settings = None
        site_name = (settings.site_name if settings and getattr(settings, "site_name", None) else "E-PROJECT")
        site_tagline = (settings.site_tagline if settings and getattr(settings, "site_tagline", None) else "Plateforme de gestion etudiants")
        site_footer_text = (settings.site_footer_text if settings and getattr(settings, "site_footer_text", None) else site_name)
        site_logo_url = (settings.site_logo_url if settings and getattr(settings, "site_logo_url", None) else "")
        workspace_name = site_name
        workspace_logo_url = site_logo_url
        it_scope_branch_id = session.get("it_scope_branch_id")
        it_scope_branches = []
        it_ui_mode = session.get("it_ui_mode", "saas")
        subscription_lock = False
        is_platform_super_admin = bool(current_user.is_authenticated and is_super_admin_platform(current_user))
        if current_user.is_authenticated:
            role = normalized_role(getattr(current_user, "role", None))
            workspace_branch_id = getattr(current_user, "branch_id", None)
            if not workspace_branch_id:
                membership = Membership.query.filter_by(user_id=current_user.id).order_by(Membership.id.asc()).first()
                if membership and membership.branch_id:
                    workspace_branch_id = membership.branch_id
            if workspace_branch_id:
                branch = Branch.query.get(workspace_branch_id)
                if branch and not is_super_admin_platform(current_user):
                    workspace_name = branch.name
                    workspace_logo_url = branch.logo_url or ""
            if role == "FOUNDER" and subscriptions_enforced() and not is_subscription_active_for_user(current_user):
                subscription_lock = True
        if is_platform_super_admin:
            it_scope_branches = Branch.query.join(AgencySubscription, AgencySubscription.branch_id == Branch.id).order_by(Branch.name.asc()).all()
            endpoint = request.endpoint or ""
            if endpoint == "dashboard.it_saas_dashboard":
                it_ui_mode = "saas"
            elif request.args.get("agency_view") == "1" or endpoint == "dashboard.it_agency_dashboard":
                it_ui_mode = "agency"
            session["it_ui_mode"] = it_ui_mode
            if it_ui_mode == "agency" and it_scope_branch_id:
                scoped = Branch.query.get(it_scope_branch_id)
                if scoped:
                    workspace_name = f"{scoped.name} (Vue Agence IT)"
                    workspace_logo_url = scoped.logo_url or ""
                else:
                    it_scope_branch_id = None
                    session.pop("it_scope_branch_id", None)
        realtime_chat_branch_id = 0
        realtime_chat_branch_name = ""
        chat_unread_alerts = 0
        chat_unread_messages = 0
        chat_notification_branch_id = 0
        subscription_unread_alerts = 0
        if current_user.is_authenticated:
            if is_platform_super_admin:
                if it_scope_branch_id:
                    realtime_chat_branch_id = int(it_scope_branch_id)
            else:
                if getattr(current_user, "branch_id", None):
                    realtime_chat_branch_id = int(current_user.branch_id)
                else:
                    membership = Membership.query.filter_by(user_id=current_user.id).order_by(Membership.id.asc()).first()
                    if membership and membership.branch_id:
                        realtime_chat_branch_id = int(membership.branch_id)
            if realtime_chat_branch_id:
                chat_branch = Branch.query.get(realtime_chat_branch_id)
                if chat_branch:
                    realtime_chat_branch_name = chat_branch.name

        if is_platform_super_admin:
            subscription_unread_alerts = (
                AgencySubscription.query.join(User, AgencySubscription.owner_user_id == User.id)
                .filter(AgencySubscription.branch_id == User.branch_id, AgencySubscription.status == "pending_review")
                .count()
            )
            pending_rows = (
                AuditLog.query.filter_by(type_event="chat_alert", action="technical_pending")
                .order_by(AuditLog.created_at.asc())
                .all()
            )
            handled_rows = (
                AuditLog.query.filter_by(type_event="chat_alert", action="technical_handled")
                .order_by(AuditLog.created_at.asc())
                .all()
            )
            latest_pending_by_branch = {}
            latest_handled_by_branch = {}
            unread_branch_candidates = {}
            for row in pending_rows:
                latest_pending_by_branch[row.branch_id] = row.created_at
            for row in handled_rows:
                latest_handled_by_branch[row.branch_id] = row.created_at
            for branch_id, pending_at in latest_pending_by_branch.items():
                handled_at = latest_handled_by_branch.get(branch_id)
                if handled_at is None or handled_at < pending_at:
                    chat_unread_alerts += 1
                    if branch_id and pending_at:
                        prev = unread_branch_candidates.get(branch_id)
                        if prev is None or prev < pending_at:
                            unread_branch_candidates[branch_id] = pending_at

            # Unread client messages by branch: last client message newer than last IT seen marker.
            client_rows = (
                AuditLog.query.filter_by(type_event="chat_message", action="client_message")
                .order_by(AuditLog.created_at.asc())
                .all()
            )
            seen_rows = (
                AuditLog.query.filter_by(type_event="chat_thread", action="it_seen")
                .order_by(AuditLog.created_at.asc())
                .all()
            )
            latest_client_by_branch = {}
            latest_seen_by_branch = {}
            for row in client_rows:
                if row.branch_id:
                    latest_client_by_branch[row.branch_id] = row.created_at
            for row in seen_rows:
                if row.branch_id:
                    latest_seen_by_branch[row.branch_id] = row.created_at
            for branch_id, client_at in latest_client_by_branch.items():
                seen_at = latest_seen_by_branch.get(branch_id)
                if seen_at is None or (client_at and seen_at < client_at):
                    chat_unread_messages += 1
                    if branch_id and client_at:
                        prev = unread_branch_candidates.get(branch_id)
                        if prev is None or prev < client_at:
                            unread_branch_candidates[branch_id] = client_at

            if unread_branch_candidates:
                chat_notification_branch_id = max(unread_branch_candidates.items(), key=lambda item: item[1])[0]
            elif realtime_chat_branch_id:
                chat_notification_branch_id = realtime_chat_branch_id
        return {
            "current_user": current_user,
            "site_name": site_name,
            "site_tagline": site_tagline,
            "site_footer_text": site_footer_text,
            "site_logo_url": site_logo_url,
            "workspace_name": workspace_name,
            "workspace_logo_url": workspace_logo_url,
            "it_scope_branch_id": it_scope_branch_id or 0,
            "it_scope_branches": it_scope_branches,
            "it_ui_mode": it_ui_mode,
            "subscription_lock": subscription_lock,
            "is_platform_super_admin": is_platform_super_admin,
            "realtime_chat_enabled": bool(realtime_chat_branch_id),
            "realtime_chat_branch_id": realtime_chat_branch_id,
            "realtime_chat_branch_name": realtime_chat_branch_name,
            "chat_unread_alerts": chat_unread_alerts,
            "chat_unread_messages": chat_unread_messages,
            "chat_notification_branch_id": chat_notification_branch_id,
            "subscription_unread_alerts": subscription_unread_alerts,
        }

    @app.before_request
    def enforce_https_in_production():
        if not app.config.get("SECURITY_FORCE_HTTPS"):
            return None
        if app.debug or app.testing:
            return None
        if request.is_secure:
            return None
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if forwarded_proto == "https":
            return None
        return redirect(request.url.replace("http://", "https://", 1), code=301)

    @app.after_request
    def set_security_headers(response):
        csp = (
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.before_request
    def enforce_password_change():
        if not current_user.is_authenticated:
            return None
        try:
            process_subscription_notifications()
        except Exception:
            pass

        # Keep branch-scoped users aligned to their membership branch.
        role_now = normalized_role(getattr(current_user, "role", None))
        if role_now in ("ADMIN_BRANCH", "EMPLOYEE"):
            rows = (
                Membership.query.filter_by(user_id=current_user.id)
                .order_by(Membership.created_at.desc(), Membership.id.desc())
                .all()
            )
            latest_branch_id = next((r.branch_id for r in rows if r.branch_id is not None), None)
            if latest_branch_id and current_user.branch_id != latest_branch_id:
                current_user.branch_id = latest_branch_id
                db.session.commit()
        if not current_user.must_change_password:
            if request.endpoint in ("auth.profile", "auth.logout", "static"):
                return None
            if email_needs_update(getattr(current_user, "email", None)):
                return redirect(url_for("auth.profile"))
            role = normalized_role(getattr(current_user, "role", None))
            subscription = get_subscription_for_user(current_user) if role in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE") else None
            is_owner = AgencySubscription.query.filter_by(owner_user_id=current_user.id).first() is not None

            # Regle prioritaire: si l'abonnement de l'agence est explicitement expire, on coupe la session.
            if subscription is not None and subscription.status == "expired":
                if role == "FOUNDER" or is_owner:
                    if request.endpoint not in ("auth.subscription_status", "auth.logout", "auth.profile", "static"):
                        flash("Abonnement agence expire. Merci de reactiver votre plan.", "warning")
                        return redirect(url_for("auth.subscription_status"))
                else:
                    if request.endpoint not in ("auth.login", "auth.logout", "static"):
                        logout_user()
                        flash("Abonnement agence expire. Connexion fermee.", "warning")
                        return redirect(url_for("auth.login"))

            if subscriptions_enforced() and role in ("FOUNDER", "ADMIN_BRANCH", "EMPLOYEE"):
                if (
                    subscription is None
                    or subscription.status != "active"
                    or (subscription.ends_at is not None and subscription.ends_at < datetime.utcnow())
                ):
                    if role == "FOUNDER" or is_owner:
                        if request.endpoint not in ("auth.subscription_status", "auth.logout", "auth.profile", "static"):
                            flash("Abonnement agence inactif. Merci de choisir un plan pour reactiver votre agence.", "warning")
                            return redirect(url_for("auth.subscription_status"))
                    else:
                        if request.endpoint not in ("auth.login", "auth.logout", "static"):
                            logout_user()
                            flash("Abonnement agence expire. Connexion fermee.", "warning")
                            return redirect(url_for("auth.login"))
            return None
        if request.endpoint in ("auth.change_password", "auth.profile", "auth.logout", "static"):
            return None
        return redirect(url_for("auth.change_password"))

    return app





def ensure_platform_it_account(app):
    """Bootstrap a platform IT account on fresh production databases.

    Env vars used:
    - BOOTSTRAP_IT_EMAIL
    - BOOTSTRAP_IT_USERNAME
    - BOOTSTRAP_IT_PASSWORD
    """
    with app.app_context():
        try:
            email = (app.config.get("BOOTSTRAP_IT_EMAIL") or "").strip().lower()
            username = (app.config.get("BOOTSTRAP_IT_USERNAME") or "").strip()
            raw_password = (app.config.get("BOOTSTRAP_IT_PASSWORD") or "").strip()

            if not email or not username or not raw_password:
                return

            # If any IT account already exists, keep current state.
            existing_it = User.query.filter(User.role.in_(["IT", "INFORMATICIEN"]))                .order_by(User.id.asc()).first()
            if existing_it:
                if existing_it.platform_role != "SUPER_ADMIN_PLATFORM":
                    existing_it.platform_role = "SUPER_ADMIN_PLATFORM"
                    existing_it.is_active = True
                    existing_it.must_change_password = False
                    db.session.commit()
                return

            by_email = User.query.filter_by(email=email).first()
            if by_email:
                by_email.username = username
                by_email.role = "IT"
                by_email.platform_role = "SUPER_ADMIN_PLATFORM"
                by_email.is_active = True
                by_email.must_change_password = False
                if not by_email.password_hash:
                    by_email.password_hash = password_hasher.hash(raw_password)
                db.session.commit()
                return

            row = User(
                username=username,
                email=email,
                password_hash=password_hasher.hash(raw_password),
                platform_role="SUPER_ADMIN_PLATFORM",
                role="IT",
                is_active=True,
                must_change_password=False,
                branch_id=None,
            )
            db.session.add(row)
            db.session.commit()
        except Exception as exc:
            app.logger.warning("IT bootstrap failed: %s", exc)


def ensure_runtime_tables(app):
    """Ensure core tables exist at runtime (useful on fresh managed Postgres)."""
    with app.app_context():
        try:
            db.create_all()
        except Exception as exc:
            app.logger.warning("Runtime db.create_all() failed: %s", exc)


def ensure_runtime_schema_compat(app):
    """Lightweight runtime patch for legacy SQLite schemas."""
    with app.app_context():
        try:
            if db.engine.dialect.name != "sqlite":
                return
            with db.engine.begin() as conn:
                # case_stages.slug back-compat
                table_exists = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='case_stages'"
                ).fetchone()
                if table_exists:
                    columns = {
                        row[1]
                        for row in conn.exec_driver_sql("PRAGMA table_info(case_stages)").fetchall()
                    }
                    if "slug" not in columns:
                        conn.exec_driver_sql("ALTER TABLE case_stages ADD COLUMN slug VARCHAR(120)")
                    conn.exec_driver_sql(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_case_stages_slug ON case_stages(slug)"
                    )

                # Multi-tenant hardening: entities.branch_id
                entities_exists = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='entities'"
                ).fetchone()
                if entities_exists:
                    entity_cols = {
                        row[1]
                        for row in conn.exec_driver_sql("PRAGMA table_info(entities)").fetchall()
                    }
                    if "branch_id" not in entity_cols:
                        conn.exec_driver_sql("ALTER TABLE entities ADD COLUMN branch_id INTEGER")
                        conn.exec_driver_sql(
                            "UPDATE entities SET branch_id = ("
                            "SELECT sc.branch_id FROM study_cases sc "
                            "WHERE sc.entity_id = entities.id AND sc.branch_id IS NOT NULL "
                            "ORDER BY sc.id DESC LIMIT 1"
                            ") WHERE branch_id IS NULL"
                        )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_entities_branch_id ON entities(branch_id)"
                    )

                # Multi-tenant hardening: schools.branch_id
                schools_exists = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schools'"
                ).fetchone()
                if schools_exists:
                    school_cols = {
                        row[1]
                        for row in conn.exec_driver_sql("PRAGMA table_info(schools)").fetchall()
                    }
                    if "branch_id" not in school_cols:
                        conn.exec_driver_sql("ALTER TABLE schools ADD COLUMN branch_id INTEGER")
                        conn.exec_driver_sql(
                            "UPDATE schools SET branch_id = ("
                            "SELECT sc.branch_id FROM study_cases sc "
                            "WHERE sc.school_id = schools.id AND sc.branch_id IS NOT NULL "
                            "ORDER BY sc.id DESC LIMIT 1"
                            ") WHERE branch_id IS NULL"
                        )
                        conn.exec_driver_sql(
                            "UPDATE schools SET branch_id = ("
                            "SELECT entities.branch_id FROM entities WHERE entities.id = schools.entity_id"
                            ") WHERE branch_id IS NULL"
                        )
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_schools_branch_id ON schools(branch_id)"
                    )

                # Student procedure mailbox fields back-compat
                students_exists = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='students'"
                ).fetchone()
                if students_exists:
                    student_cols = {
                        row[1]
                        for row in conn.exec_driver_sql("PRAGMA table_info(students)").fetchall()
                    }
                    if "procedure_email" not in student_cols:
                        conn.exec_driver_sql("ALTER TABLE students ADD COLUMN procedure_email VARCHAR(120)")
                    if "procedure_email_password" not in student_cols:
                        conn.exec_driver_sql("ALTER TABLE students ADD COLUMN procedure_email_password VARCHAR(255)")
        except Exception:
            # Keep app boot resilient; migration can still be run later.
            return

def ensure_default_entities(app):
    # Aucun seed partenaire/ecole en dur: les donnees doivent etre saisies par chaque agence.
    return


def register_cli(app):
    @app.cli.command("process-subscriptions")
    def process_subscriptions_job():
        """Execution planifiee: expire abonnements + envoie notifications."""
        process_subscription_notifications()
        print("Subscription notifications processed.")

    @app.cli.command("seed-entities")
    def seed_entities():
        print("Aucun seed partenaire/ecole par defaut. Cree les entites depuis le dashboard agence.")

    @app.cli.command("create-default-users")
    def create_default_users():
        branch = Branch.query.filter_by(country_code="CI", name="InnovFormation Cote d'Ivoire").first()
        if branch is None:
            branch = Branch(
                name="InnovFormation Cote d'Ivoire",
                country_code="CI",
                city="Abidjan",
                timezone="Africa/Abidjan",
            )
            db.session.add(branch)
            db.session.commit()
            print("Branch created: InnovFormation Cote d'Ivoire")

        founder_email = "founder@innovformation"
        founder = User.query.filter_by(email=founder_email).first()
        if founder is None:
            founder = User(
                username="founder",
                email=founder_email,
                role="FOUNDER",
                branch_id=None,
                password_hash=password_hasher.hash("Founder@123"),
                is_active=True,
                must_change_password=True,
            )
            db.session.add(founder)
            print("FOUNDER created: founder@innovformation / Founder@123")

        admin_email = "admin-ci@innovformation"
        admin = User.query.filter_by(email=admin_email).first()
        if admin is None:
            admin = User(
                username="admin_ci",
                email=admin_email,
                role="ADMIN_BRANCH",
                branch_id=branch.id,
                password_hash=password_hasher.hash("AdminCI@123"),
                is_active=True,
                must_change_password=True,
            )
            db.session.add(admin)
            print("ADMIN_BRANCH created: admin-ci@innovformation / AdminCI@123")

        db.session.commit()
        print("Default users ready.")

    @app.cli.command("create-admin")
    def create_admin_legacy():
        print("Commande legacy desactivee. Utilisez create-default-users ou le module Utilisateurs.")

    @app.cli.command("disable-legacy-admin")
    def disable_legacy_admin():
        user = User.query.filter_by(username="admin").first()
        if not user:
            print("Legacy admin introuvable.")
            return
        user.is_active = False
        user.must_change_password = True
        db.session.commit()
        print(f"Legacy admin desactive: {user.username} ({user.email})")

    @app.cli.command("delete-legacy-admin")
    def delete_legacy_admin():
        user = User.query.filter_by(username="admin").first()
        if not user:
            print("Legacy admin introuvable.")
            return
        db.session.delete(user)
        db.session.commit()
        print("Legacy admin supprime.")

    @app.cli.command("backfill-student-auth")
    def backfill_student_auth():
        created = 0
        for student in Student.query.order_by(Student.id.asc()).all():
            if StudentAuth.query.filter_by(student_id=student.id).first():
                continue
            temp_password = f"Temp{student.id:04d}IF"
            row = StudentAuth(
                student_id=student.id,
                password_hash=password_hasher.hash(temp_password),
                must_change_password=True,
            )
            db.session.add(row)
            created += 1
            print(f"{student.matricule} -> {temp_password}")
        db.session.commit()
        print(f"Student auth created: {created}")

    @app.cli.command("backfill-student-branches")
    def backfill_student_branches():
        from app.models import AuditLog

        unassigned = Student.query.filter(Student.branch_id.is_(None)).order_by(Student.id.asc()).all()
        if not unassigned:
            print("No unassigned students.")
            return

        branches = Branch.query.order_by(Branch.id.asc()).all()
        fallback_branch_id = branches[0].id if branches else None
        if fallback_branch_id is None:
            print("No branch available. Abort.")
            return

        assigned = 0
        from_audit = 0
        from_fallback = 0

        for student in unassigned:
            branch_id = None
            audit = (
                AuditLog.query.filter(
                    AuditLog.student_id == student.id,
                    AuditLog.action.in_(["student_create", "student_import", "student_update"]),
                    AuditLog.branch_id.isnot(None),
                )
                .order_by(AuditLog.id.desc())
                .first()
            )
            if audit and audit.branch_id:
                branch_id = audit.branch_id
                from_audit += 1
            else:
                branch_id = fallback_branch_id
                from_fallback += 1

            student.branch_id = branch_id
            assigned += 1

        db.session.commit()
        print(f"Backfill done. Assigned: {assigned}, from_audit: {from_audit}, from_fallback: {from_fallback}.")

    def _next_unique_branch_name(base_name):
        name = base_name
        idx = 2
        while Branch.query.filter_by(name=name).first() is not None:
            name = f"{base_name} {idx}"
            idx += 1
        return name

    @app.cli.command("check-founder-isolation")
    def check_founder_isolation():
        founders = User.query.filter_by(role="FOUNDER").order_by(User.id.asc()).all()
        if not founders:
            print("Aucun compte FOUNDER.")
            return

        issues = 0
        for founder in founders:
            owner_sub = AgencySubscription.query.filter_by(owner_user_id=founder.id).first()
            branch_sub = AgencySubscription.query.filter_by(branch_id=founder.branch_id).first() if founder.branch_id else None
            problem = None

            if owner_sub and owner_sub.branch_id:
                if founder.branch_id != owner_sub.branch_id:
                    problem = f"branche founder={founder.branch_id} differente de sub={owner_sub.branch_id}"
            else:
                if founder.branch_id is None:
                    problem = "pas de branche et pas de subscription"
                elif branch_sub and branch_sub.owner_user_id != founder.id:
                    problem = f"branche {founder.branch_id} appartient deja a owner #{branch_sub.owner_user_id}"
                else:
                    problem = "pas de subscription owner"

            if problem:
                issues += 1
                print(f"[ISSUE] founder#{founder.id} {founder.email}: {problem}")

        print(f"Controle termine. Founders: {len(founders)}, issues: {issues}")

    @app.cli.command("fix-founder-isolation")
    def fix_founder_isolation():
        settings = get_or_create_portal_settings()
        founders = User.query.filter_by(role="FOUNDER").order_by(User.id.asc()).all()
        if not founders:
            print("Aucun compte FOUNDER.")
            return

        changed = 0
        created_branches = 0
        created_subs = 0

        for founder in founders:
            owner_sub = AgencySubscription.query.filter_by(owner_user_id=founder.id).first()
            branch_sub = AgencySubscription.query.filter_by(branch_id=founder.branch_id).first() if founder.branch_id else None

            target_branch_id = None

            if owner_sub and owner_sub.branch_id:
                target_branch_id = owner_sub.branch_id
            else:
                can_reuse_current_branch = founder.branch_id is not None and (branch_sub is None or branch_sub.owner_user_id == founder.id)

                if can_reuse_current_branch:
                    target_branch_id = founder.branch_id
                else:
                    base_name = f"Agence {founder.display_name or founder.username or founder.id}"
                    branch_name = _next_unique_branch_name(base_name)
                    new_branch = Branch(
                        name=branch_name,
                        country_code="CI",
                        city="Abidjan",
                        timezone="Africa/Abidjan",
                    )
                    db.session.add(new_branch)
                    db.session.flush()
                    target_branch_id = new_branch.id
                    created_branches += 1

                if owner_sub is None:
                    amount, currency = price_for_plan(settings, "starter")
                    sub_status = "pending"
                    starts_at = None
                    paid_at = None
                    if not subscriptions_enforced(settings):
                        sub_status = "active"
                        starts_at = datetime.utcnow()
                        paid_at = starts_at

                    owner_sub = AgencySubscription(
                        branch_id=target_branch_id,
                        owner_user_id=founder.id,
                        plan_code="starter",
                        amount=amount,
                        currency=currency,
                        status=sub_status,
                        starts_at=starts_at,
                        ends_at=None,
                        paid_at=paid_at,
                    )
                    db.session.add(owner_sub)
                    created_subs += 1
                else:
                    owner_sub.branch_id = target_branch_id

            if founder.branch_id != target_branch_id:
                founder.branch_id = target_branch_id
                changed += 1

        db.session.commit()
        print(
            f"Correction terminee. Founders: {len(founders)}, moved: {changed}, new_branches: {created_branches}, new_subscriptions: {created_subs}"
        )

    @app.cli.command("backfill-tenant-memberships")
    def backfill_tenant_memberships():
        created = 0
        updated_super_admin = 0

        # Keep exactly one global platform chief (the existing IT account(s) can be normalized manually after).
        first_it = User.query.filter(User.role.in_(["IT", "INFORMATICIEN"])).order_by(User.id.asc()).first()
        if first_it and first_it.platform_role != "SUPER_ADMIN_PLATFORM":
            first_it.platform_role = "SUPER_ADMIN_PLATFORM"
            updated_super_admin += 1

        for user in User.query.order_by(User.id.asc()).all():
            if user.branch_id:
                existing = Membership.query.filter_by(user_id=user.id, branch_id=user.branch_id).first()
                if existing is None:
                    m_role = "OWNER" if normalized_role(user.role) == "FOUNDER" else "STAFF"
                    db.session.add(Membership(user_id=user.id, branch_id=user.branch_id, role=m_role))
                    created += 1

        for branch in Branch.query.order_by(Branch.id.asc()).all():
            if not branch.slug:
                base = (branch.name or "agence").strip().lower()
                base = "-".join(filter(None, ["".join(ch if ch.isalnum() else " " for ch in base).strip().replace("  ", " ").replace(" ", "-")])) or f"agence-{branch.id}"
                slug = base
                i = 2
                while Branch.query.filter(Branch.slug == slug, Branch.id != branch.id).first() is not None:
                    slug = f"{base}-{i}"
                    i += 1
                branch.slug = slug

        db.session.commit()
        print(f"Done. memberships_created={created}, super_admin_updated={updated_super_admin}")




















