import unittest

from app import create_app
from app.extensions import db


class SmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            RATELIMIT_ENABLED=False,
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json.get("status"), "ok")

    def test_auth_login_page_accessible(self):
        resp = self.client.get("/auth/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Se connecter", resp.data)

    def test_students_requires_auth(self):
        resp = self.client.get("/students/", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/auth/login", resp.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()
