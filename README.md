# InnovFormation (Flask)

CRM InnovFormation multi-branches avec:
- Authentification + roles (`FOUNDER`, `ADMIN_BRANCH`, `EMPLOYEE`, `IT`, portail `STUDENT`)
- Gestion etudiants + parents + documents
- Procedures etranger (entites, ecoles, dossiers, timeline)
- RDV par evenements (slots, tokens, capacite par jour)
- Emails pros (composer libre, logos, bouton RDV, logs SMTP)
- Dashboards (branche + fondateur global)
- Commissions partenaires + suivi arrivee/logement/mentor
- Landing page SaaS + abonnement agences (plans dynamiques)

## 1) Lancement local Windows
```powershell
cd C:\code.py\InnovFormation
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
$env:FLASK_APP="run.py"
flask db upgrade
flask create-default-users
flask backfill-student-auth
flask run --host 127.0.0.1 --port 5000
```

Acces:
- Accueil: `http://127.0.0.1:5000/`
- Admin login: `http://127.0.0.1:5000/auth/login`
- Portail etudiant: `http://127.0.0.1:5000/student/login`

## 2) Comptes par defaut
`flask create-default-users` cree:
- `founder@innovformation` / `Founder@123` (role `FOUNDER`)
- `admin-ci@innovformation` / `AdminCI@123` (role `ADMIN_BRANCH`)

Ces comptes sont forces au changement de mot de passe a la premiere connexion.

## 3) Variables d'environnement
Voir `.env.example`.

En production (Render/Railway), definir au minimum:
- `SECRET_KEY`
- `DATABASE_URL` (PostgreSQL)
- SMTP si envoi reel:
  - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS`
  - valeur recommandee plateforme IT: `SMTP_FROM=eudyproject@gmail.com`

## 4) Tests essentiels
```powershell
cd C:\code.py\InnovFormation
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests couverts:
- `/health`
- acces page login
- protection auth sur `/students/`

## 5) Deploiement Render (PostgreSQL) + lien public

### Option A: Blueprint (recommande)
1. Pousser le repo sur GitHub.
2. Sur Render: `New` -> `Blueprint`.
3. Choisir le repo (Render lit `render.yaml`).
4. Une fois deploye, ouvrir le **Shell Render** et lancer:
```bash
flask db upgrade
flask create-default-users
flask backfill-student-auth
```
5. Recuperer l'URL publique dans Render (ex: `https://innovformation-web.onrender.com`).

### Option B: Web Service manuel
1. `New` -> `Web Service` (Python).
2. Build command:
```bash
pip install -r requirements.txt
```
3. Start command:
```bash
gunicorn run:app
```
4. Attacher une base PostgreSQL Render et mapper `DATABASE_URL`.
5. Ajouter `SECRET_KEY` (+ SMTP si besoin).
6. Shell Render:
```bash
flask db upgrade
flask create-default-users
flask backfill-student-auth
```
7. Ouvrir l'URL publique Render du service.

## 6) Logos partenaires (emails)
Deposer les logos dans:
- `app/static/partners/uco.png`
- `app/static/partners/cie.png`
- `app/static/partners/studentgator.png`

Les emails utilisent des URLs publiques HTTPS de ces fichiers en production:
- `https://<votre-app>.onrender.com/static/partners/uco.png`
- `https://<votre-app>.onrender.com/static/partners/cie.png`
- `https://<votre-app>.onrender.com/static/partners/studentgator.png`

`Campus France` est gere en texte (pas d'image).

## 7) Commandes utiles
```powershell
$env:FLASK_APP="run.py"
flask routes
flask db migrate -m "message"
flask db upgrade
flask create-default-users
flask backfill-student-auth
```

## 9) Abonnements SaaS (nouveau)
- La page d'accueil (`/`) affiche les plans Starter/Pro/Enterprise.
- Les prix viennent de `Parametres IT`:
  - si non renseignes, chaque plan affiche `0`.
- Un compte agence se cree depuis la landing (role `ADMIN_BRANCH`) et passe en statut abonnement `pending`.
- L'IT active l'abonnement dans `Parametres IT > Abonnements`.
- Si abonnement expire:
  - l'agence est redirigee vers `/auth/subscription`
  - des mails de rappel expiration sont emis automatiquement (si SMTP configure).

## 8) Notes securite
- CSRF actif (Flask-WTF)
- Hash mot de passe Argon2
- Login rate-limit (5/min)
- Uploads limites par extensions + `MAX_CONTENT_LENGTH`
- Permissions role + branch appliquees sur les modules principaux
