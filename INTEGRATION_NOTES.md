# Integration Notes

This package is the integrated SQLAlchemy 2.0 AI-template version.

## Base version

- Uses the clean, tested SQLAlchemy 2.0 async rewrite as the base.
- Keeps the v6 scope: no Tortoise ORM, no Aerich, no traditional RBAC, no ServicePermission dead code, no CTX_USER_ID, no sensitive-word half module.

## Cross-check improvements absorbed

- User management routes are RESTful:
  - `GET /api/v1/users/`
  - `POST /api/v1/users/`
  - `GET /api/v1/users/{user_id}`
  - `PUT /api/v1/users/{user_id}`
  - `DELETE /api/v1/users/{user_id}`
  - `POST /api/v1/users/{user_id}/reset-password`
- User update now takes `user_id` from the path instead of the body.
- User update performs duplicate checks for username, email, and phone.
- Response helpers now run content through `jsonable_encoder`, avoiding datetime serialization issues.
- Smoke tests were updated for RESTful users routes and `/users/me`.

## Validation performed

- `pytest`: 10 passed
- `python -m compileall`: passed
- `PYTHONPATH=src python -c "from src import app"`: passed
- `PYTHONPATH=src alembic upgrade head`: passed
- Final cleanup removed runtime files: `__pycache__`, `.pytest_cache`, `*.db`, logs, uploads.
