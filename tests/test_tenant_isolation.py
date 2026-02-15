from datetime import datetime

from flask_login import login_user, logout_user

from app import create_app
from app.extensions import db
from app.models import Branch, Membership, Student, User
from app.utils.authz import scope_query_by_branch


class TestConfig:
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    WTF_CSRF_ENABLED = False


def _mk_user(username, email):
    return User(
        username=username,
        email=email,
        password_hash="x",
        role="FOUNDER",
        is_active=True,
        must_change_password=False,
    )


def _mk_student(branch_id, matricule):
    return Student(
        branch_id=branch_id,
        matricule=matricule,
        nom="Test",
        prenoms="User",
        sexe="M",
        filiere="IDA",
        niveau="L1",
        promotion="2026",
    )


def test_isolation_between_agencies():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()

        agency_a = Branch(name="Agency A", slug="agency-a", country_code="CI")
        agency_b = Branch(name="Agency B", slug="agency-b", country_code="CI")
        db.session.add_all([agency_a, agency_b])
        db.session.flush()

        user_a = _mk_user("owner_a", "a@test.local")
        user_b = _mk_user("owner_b", "b@test.local")
        db.session.add_all([user_a, user_b])
        db.session.flush()

        db.session.add_all(
            [
                Membership(user_id=user_a.id, branch_id=agency_a.id, role="OWNER"),
                Membership(user_id=user_b.id, branch_id=agency_b.id, role="OWNER"),
                _mk_student(agency_a.id, "IF-2026-90001"),
            ]
        )
        db.session.commit()

        with app.test_request_context("/"):
            login_user(user_b)
            rows_b = scope_query_by_branch(Student.query, Student).all()
            assert len(rows_b) == 0
            logout_user()


def test_soft_deleted_student_not_visible():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()

        agency_a = Branch(name="Agency A", slug="agency-a", country_code="CI")
        db.session.add(agency_a)
        db.session.flush()

        user_a = _mk_user("owner_a", "a@test.local")
        db.session.add(user_a)
        db.session.flush()
        db.session.add(Membership(user_id=user_a.id, branch_id=agency_a.id, role="OWNER"))

        student = _mk_student(agency_a.id, "IF-2026-90002")
        db.session.add(student)
        db.session.commit()

        student.deleted_at = datetime.utcnow()
        db.session.commit()

        with app.test_request_context("/"):
            login_user(user_a)
            rows = scope_query_by_branch(Student.query, Student).all()
            assert len(rows) == 0
            logout_user()


def test_new_agency_starts_empty():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()

        agency_a = Branch(name="Agency A", slug="agency-a", country_code="CI")
        agency_b = Branch(name="Agency B", slug="agency-b", country_code="CI")
        db.session.add_all([agency_a, agency_b])
        db.session.flush()

        user_a = _mk_user("owner_a", "a@test.local")
        user_b = _mk_user("owner_b", "b@test.local")
        db.session.add_all([user_a, user_b])
        db.session.flush()

        db.session.add(Membership(user_id=user_a.id, branch_id=agency_a.id, role="OWNER"))
        db.session.add(Membership(user_id=user_b.id, branch_id=agency_b.id, role="OWNER"))
        db.session.add(_mk_student(agency_a.id, "IF-2026-90003"))
        db.session.commit()

        with app.test_request_context("/"):
            login_user(user_b)
            rows_b = scope_query_by_branch(Student.query, Student).all()
            assert len(rows_b) == 0
            logout_user()
