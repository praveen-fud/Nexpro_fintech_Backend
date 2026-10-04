FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# app.bootstrap only ensures real config (fee rules, limits) exists - no
# accounts, no fixtures. app.seed (demo accounts + fake data) is dev-only
# and is never run here; create production accounts with app.create_admin
# instead (run manually, once, via shell access to this service).
CMD ["sh", "-c", "alembic upgrade head && python -m app.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
