# Bulk Certificate Generation API

A Python/Flask backend that accepts one bulk request, validates recipients, generates a PDF certificate for each valid recipient, tracks job progress, and lets clients download each generated certificate.

## Technology

- Python 3.10+
- Flask REST API
- Flask-SQLAlchemy with SQLite by default (relational database)
- ReportLab for PDF generation
- pytest for automated tests

## Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Run the application

```bash
python app.py
```

The API runs at `http://127.0.0.1:5000`. The database defaults to SQLite and is created automatically. For local development, generated PDFs are stored under Flask's `instance/certificates/` directory.

Optional environment variables:

- `DATABASE_URL`: SQLAlchemy database URL, e.g. `sqlite:///certificates.db` or a PostgreSQL URL if its driver is installed.
- `CERTIFICATE_DIR`: directory used to store generated PDFs.

## Run tests

```bash
pytest -q
```

Tests use a temporary SQLite database and temporary certificate directory. They process jobs inline so assertions are deterministic.

## Submit a bulk request

One request can include many recipients (maximum 5,000 per job). Example:

```bash
curl -X POST http://127.0.0.1:5000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Annual Tech Workshop",
    "course_name": "Introduction to Python",
    "issue_date": "2026-10-11",
    "recipients": [
      {"name": "Asha Kumar", "email": "asha@example.com"},
      {"name": "Ravi Kumar", "email": "ravi@example.com"},
      {"name": "", "email": "invalid"}
    ]
  }'
```

The API responds with HTTP `202 Accepted` and a `job_id` and status URL. Invalid recipients are recorded as failed entries; valid recipients continue to generate certificates.

## Check status and progress

```bash
curl http://127.0.0.1:5000/api/jobs/JOB_ID
```

The response includes overall status (`queued`, `running`, `completed`, `partial_failed`, or `failed`), total/succeeded/failed/pending counts, recipient-level statuses, errors, and download URLs for successful certificates.

## Retrieve a certificate

Use the `recipient_id` from the job status response:

```bash
curl -L \
  http://127.0.0.1:5000/api/jobs/JOB_ID/certificates/RECIPIENT_ID \
  --output certificate.pdf
```

The endpoint returns a PDF download for successful generations. It returns `409 Conflict` if generation has not succeeded, and `404 Not Found` for an unknown recipient/job or missing file.

## API summary

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/api/jobs` | Submit a bulk certificate job |
| `GET` | `/api/jobs/<job_id>` | Read job progress and recipient results |
| `GET` | `/api/jobs/<job_id>/certificates/<recipient_id>` | Download one generated PDF |

## Design decisions

1. **Bulk-first API:** the client submits all recipients in one request instead of making one request per certificate.
2. **Background processing:** in normal mode, the API returns `202 Accepted` and a background thread processes the job. This keeps the request path short while certificates are generated. For a production deployment with multiple workers or high volumes, replace the in-process thread with a durable queue such as Celery/RQ and Redis; in-process threads do not survive a process restart.
3. **Failure isolation:** each recipient is processed in its own `try/except`. One failed PDF does not stop other recipients. Invalid input is saved as a failed recipient with an explanation.
4. **Single template:** `render_certificate()` is the one predefined PDF template. Edit this function to change the design or certificate fields.
5. **Relational persistence:** jobs and recipients are stored in SQL tables, with recipient rows linked to a job. SQLite is the default for simple setup; a production database can be configured through `DATABASE_URL`.
6. **Progress tracking:** the job status endpoint computes pending/processing counts and reports successful and failed counts.
7. **Input limits:** required fields and a 5,000-recipient maximum protect the API from accidentally oversized requests.

## Status meanings

- `queued`: accepted but not yet processing
- `running`: at least one recipient is being processed
- `completed`: all recipients generated successfully
- `partial_failed`: at least one succeeded and at least one failed
- `failed`: no recipient generated successfully

## Notes / production hardening

This is a runnable interview-sized implementation. For production use, add authentication/authorization, rate limiting, request-size limits at the reverse proxy, retention/cleanup rules, structured logging, durable task queueing, and a migration tool such as Alembic. Email is validated with a basic format check rather than a full RFC email parser.
