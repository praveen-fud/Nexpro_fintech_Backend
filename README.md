# Nexpro Paytech — Backend

FastAPI backend for **Nexpro Paytech**, a wallet funding platform (Credit Card / UPI / Bank Transfer → Operations review → ledger-backed wallet credit).

This is the backend only. It serves `/api/v1` to a separate frontend (see the frontend repo / `Frontend/` in the monorepo this was split from). Implements Stage 1 + the core of Stage 2 of the product spec: real auth, a real database, and an append-only wallet ledger — never a mutable balance column.

## Stack

- FastAPI, SQLAlchemy 2.x (async), Alembic
- Pydantic v2, pydantic-settings
- Argon2id password hashing, PyJWT (access tokens + rotating refresh-token cookies)
- SQLite by default (zero install, `dev.db`) or PostgreSQL via `DATABASE_URL`

## Getting started

Requires Python 3.13+ (tested on Python 3.14).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.seed          # demo users + sample data
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

The API serves on `http://localhost:8000`, with `/health` for a liveness check and `/api/v1/...` for every resource. CORS defaults to allowing `http://localhost:5173` (see `.env.example`).

```powershell
.\.venv\Scripts\python.exe -m pytest   # 7 tests covering the spec's critical cases
```

## Structure

```
app/
  core/          settings, security config
  models/        SQLAlchemy models
  schemas/       Pydantic request/response models
  repositories/  data access
  services/      business logic (ledger, funding state machine, fees)
  providers/     pluggable payment-method providers
  ledger/        append-only wallet ledger primitives
  routers/       one router per resource: auth, users, wallet, kyc, funding,
                 transactions, support, limits, operations
  security/      password hashing, JWT issuing/verification
  seed.py        demo users, fee rules, limits, sample wallet/funding data
alembic/         migrations
tests/           pytest suite
```

## Demo accounts

Seeded by `app.seed` (password `Password123` for all):

- `customer@nexpro.test` — CUSTOMER, KYC approved
- `ops@nexpro.test` — OPERATIONS
- `admin@nexpro.test` — SUPER_ADMIN

## Roles & critical invariants

Three roles: `CUSTOMER`, `OPERATIONS`, `SUPER_ADMIN`. Every request is authorized server-side (route guards on the frontend are a UX convenience only). The wallet balance is always derived from the ledger, never stored as a mutable column; a funding request can only be approved once (idempotency-key protected); rejected funding never credits a wallet; a customer can never read another customer's wallet.
