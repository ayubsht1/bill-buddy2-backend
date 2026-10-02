# BillBuddy Backend

Django REST API for authentication, friendships, groups, shared expenses, and settlements.
MySQL remains the application database and runs outside Docker. Docker Compose is only
used for the optional Redis channel layer.

## Local setup (Windows)

1. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Create a local environment file from the example and update the MySQL connection:

   ```powershell
   Copy-Item .env.example .env
   ```

   Create the `DATABASE_NAME` database and a MySQL user with access to it before
   running Django. MySQL is not included in Compose. Never commit `.env`.

3. Start Redis if you want Redis-backed WebSocket channels:

   ```powershell
   docker compose up -d redis
   ```

   Redis is bound to `localhost:6379` and has a health check. Set `REDIS_URL` in
   `.env` to `redis://127.0.0.1:6379/0` to enable it. Without `REDIS_URL`, Django
   uses its in-memory channel layer.

4. Apply the non-destructive schema migrations and run the API:

   ```powershell
   python manage.py migrate
   python manage.py runserver
   ```

   Configure `GOOGLE_CLIENT_ID` to enable Google ID-token login. Email uses Django's
   console backend by default; configure an SMTP `EMAIL_BACKEND` and the related
   `EMAIL_*` variables when sending real mail.

## API endpoints

Authenticated endpoints require `Authorization: Bearer <access-token>`.

| Area | Endpoints |
| --- | --- |
| Authentication | `POST /api/register/`, `/api/login/`, `/api/google-login/`, `/api/logout/`, `/api/token/refresh/`, `/api/token/verify/`; `GET /api/email-verify/`; `POST /api/password-reset/`, `/api/reset-password/`, `/api/resend-verification/` |
| Profile | `GET/PATCH /api/profile/`, `POST /api/change-password/` |
| Friends | `GET /api/friends/search/?q=...`, `GET /api/friends/requests/`, `POST /api/friends/requests/send/`, `POST /api/friends/requests/<request_id>/accept/`, `/reject/`, or `/cancel/`; `GET /api/friends/`, `GET /api/friends/<friend_id>/`, `DELETE /api/friends/<friend_id>/remove/` |
| Groups | `GET/POST /api/groups/`, `GET/PATCH/PUT/DELETE /api/groups/<group_id>/`, `POST /api/groups/join/`; `POST /api/groups/<group_id>/add-member/`, `DELETE /api/groups/<group_id>/members/<user_id>/`, `PATCH /api/groups/<group_id>/members/<user_id>/role/`, `POST /api/groups/<group_id>/transfer-ownership/`, `GET/POST/PATCH /api/groups/<group_id>/chat/` |
| Group expenses | `GET/POST /api/expenses/group/<group_id>/`, `GET /api/expenses/group/<group_id>/balances/`, `GET/PATCH/PUT/DELETE /api/expenses/<expense_id>/` |
| Settlements | `GET/POST /api/settlements/group/<group_id>/`, `DELETE /api/settlements/<settlement_id>/` |
| Personal expenses | `GET/POST /api/expenses/personal/`, `GET/PATCH/DELETE /api/expenses/personal/<id>/` |

Group splits accept `EQUAL`, `EXACT` (`user_id` and `amount` entries), or `PERCENT`
(`user_id` and `percentage` entries). Participants must be group members; custom
split totals must equal the expense total, percentages must total 100%, and equal
splits distribute leftover cents exactly.

Settlement records are applied immediately (there is no recipient approval
workflow). A payer must be the authenticated user, the recipient must be in the
group, and the payment must follow a currently suggested greedy debtor/creditor
pair without exceeding that pair's suggested amount. Only the payer can delete
their settlement record.

## Tests

Run the backend tests on an isolated, in-memory SQLite database so the configured
MySQL database and its data are not touched:

```powershell
$env:DB_ENGINE='django.db.backends.sqlite3'
$env:DATABASE_NAME=':memory:'
$env:REDIS_URL=''
python manage.py test bill_buddy groups expense settlement
```

The suite covers friendship workflows and authorization, group permissions, split
validation and balance calculations, expense editing/deletion, and settlement limits.
