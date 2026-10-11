import os
import threading
import uuid
from datetime import datetime, date
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas

db = SQLAlchemy()
_executor_lock = threading.Lock()


class GenerationJob(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    event_name = db.Column(db.String(200), nullable=False)
    course_name = db.Column(db.String(200), nullable=False)
    issue_date = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="queued")
    total = db.Column(db.Integer, nullable=False, default=0)
    succeeded = db.Column(db.Integer, nullable=False, default=0)
    failed = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Recipient(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    job_id = db.Column(db.String(36), db.ForeignKey("generation_job.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    error = db.Column(db.String(500), nullable=True)
    certificate_path = db.Column(db.String(500), nullable=True)


def validate_recipient(item):
    if not isinstance(item, dict):
        return None, "Recipient must be a JSON object."
    name = item.get("name")
    email = item.get("email")
    if not isinstance(name, str) or not name.strip():
        return None, "name is required and must be a non-empty string."
    if len(name.strip()) > 200:
        return None, "name must be 200 characters or fewer."
    if not isinstance(email, str) or not email.strip() or "@" not in email.strip():
        return None, "email is required and must look like an email address."
    if len(email.strip()) > 254:
        return None, "email must be 254 characters or fewer."
    return {"name": name.strip(), "email": email.strip()}, None


def render_certificate(path, recipient_name, course_name, event_name, issue_date):
    """Render one certificate using the application's single predefined PDF design."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    page = landscape(A4)
    pdf = canvas.Canvas(str(path), pagesize=page)
    width, height = page

    # Simple, reusable certificate template.
    pdf.setFillColorRGB(0.96, 0.97, 0.99)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setStrokeColorRGB(0.12, 0.27, 0.48)
    pdf.setLineWidth(5)
    pdf.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)
    pdf.setStrokeColorRGB(0.78, 0.62, 0.25)
    pdf.setLineWidth(1.5)
    pdf.rect(34, 34, width - 68, height - 68, fill=0, stroke=1)

    pdf.setFillColorRGB(0.12, 0.27, 0.48)
    pdf.setFont("Helvetica-Bold", 29)
    pdf.drawCentredString(width / 2, height - 105, "CERTIFICATE OF COMPLETION")
    pdf.setFillColorRGB(0.25, 0.28, 0.33)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(width / 2, height - 150, "This certificate is proudly presented to")

    pdf.setFillColorRGB(0.12, 0.27, 0.48)
    pdf.setFont("Helvetica-Bold", 27)
    pdf.drawCentredString(width / 2, height - 198, recipient_name)

    pdf.setFillColorRGB(0.25, 0.28, 0.33)
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(width / 2, height - 235, "for successfully completing")
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawCentredString(width / 2, height - 267, course_name)
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(width / 2, height - 295, f"Event: {event_name}")
    pdf.drawCentredString(width / 2, 72, f"Issued on: {issue_date}")
    pdf.setStrokeColorRGB(0.25, 0.28, 0.33)
    pdf.line(width / 2 - 80, 105, width / 2 + 80, 105)
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(width / 2, 89, "Authorized Signature")
    pdf.save()


def process_job(app, job_id):
    with app.app_context():
        job = db.session.get(GenerationJob, job_id)
        if not job:
            return
        job.status = "running"
        db.session.commit()

        recipients = Recipient.query.filter_by(job_id=job_id).all()
        for recipient in recipients:
            # Invalid records are stored as failed at submission time.
            if recipient.status == "failed":
                continue
            try:
                recipient.status = "processing"
                db.session.commit()
                output_dir = Path(app.config["CERTIFICATE_DIR"]) / job_id
                output_path = output_dir / f"{recipient.id}.pdf"
                render_certificate(
                    output_path, recipient.name, job.course_name,
                    job.event_name, job.issue_date
                )
                recipient.status = "succeeded"
                recipient.certificate_path = str(output_path)
                recipient.error = None
            except Exception as exc:  # isolate one certificate's failure from the rest
                recipient.status = "failed"
                recipient.error = f"Certificate generation failed: {type(exc).__name__}"
                recipient.certificate_path = None
            db.session.commit()

        job = db.session.get(GenerationJob, job_id)
        rows = Recipient.query.filter_by(job_id=job_id).all()
        job.succeeded = sum(r.status == "succeeded" for r in rows)
        job.failed = sum(r.status == "failed" for r in rows)
        if job.failed and job.succeeded:
            job.status = "partial_failed"
        elif job.failed:
            job.status = "failed"
        else:
            job.status = "completed"
        db.session.commit()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///certificates.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        CERTIFICATE_DIR=os.getenv("CERTIFICATE_DIR", str(Path(app.instance_path) / "certificates")),
        PROCESS_INLINE=False,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["CERTIFICATE_DIR"]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)

    with app.app_context():
        db.create_all()

    def schedule(job_id):
        if app.config.get("PROCESS_INLINE"):
            process_job(app, job_id)
        else:
            thread = threading.Thread(target=process_job, args=(app, job_id), daemon=True)
            thread.start()

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/api/jobs")
    def create_job():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object."}), 400

        event_name = payload.get("event_name")
        course_name = payload.get("course_name")
        recipients_data = payload.get("recipients")
        issue_date = payload.get("issue_date") or date.today().isoformat()

        if not isinstance(event_name, str) or not event_name.strip():
            return jsonify({"error": "event_name is required."}), 400
        if not isinstance(recipients_data, list) or not recipients_data:
            return jsonify({"error": "recipients must be a non-empty list."}), 400
        if not isinstance(course_name, str) or not course_name.strip():
            return jsonify({"error": "course_name is required."}), 400
        if len(recipients_data) > 5000:
            return jsonify({"error": "A job may contain at most 5000 recipients."}), 400
        try:
            date.fromisoformat(issue_date)
        except (TypeError, ValueError):
            return jsonify({"error": "issue_date must use YYYY-MM-DD format."}), 400

        job_id = str(uuid.uuid4())
        job = GenerationJob(
            id=job_id, event_name=event_name.strip(), course_name=course_name.strip(),
            issue_date=issue_date, status="queued", total=len(recipients_data),
            succeeded=0, failed=0
        )
        db.session.add(job)
        for item in recipients_data:
            valid, error = validate_recipient(item)
            if valid:
                row = Recipient(id=str(uuid.uuid4()), job_id=job_id,
                                name=valid["name"], email=valid["email"], status="pending")
            else:
                # Keep invalid recipients visible in job results, without attempting PDF generation.
                raw_name = item.get("name", "") if isinstance(item, dict) else ""
                raw_email = item.get("email", "") if isinstance(item, dict) else ""
                row = Recipient(id=str(uuid.uuid4()), job_id=job_id,
                                name=str(raw_name)[:200] or "Invalid recipient",
                                email=str(raw_email)[:254] or "invalid",
                                status="failed", error=error)
                job.failed += 1
            db.session.add(row)
        db.session.commit()
        schedule(job_id)
        return jsonify({"job_id": job_id, "status": job.status,
                        "total": job.total, "status_url": f"/api/jobs/{job_id}"}), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        job = db.session.get(GenerationJob, job_id)
        if not job:
            return jsonify({"error": "Job not found."}), 404
        rows = Recipient.query.filter_by(job_id=job_id).all()
        return jsonify({
            "job_id": job.id, "event_name": job.event_name, "course_name": job.course_name,
            "issue_date": job.issue_date, "status": job.status, "total": job.total,
            "succeeded": job.succeeded, "failed": job.failed,
            "pending": sum(r.status in ("pending", "processing") for r in rows),
            "recipients": [{
                "recipient_id": r.id, "name": r.name, "email": r.email,
                "status": r.status, "error": r.error,
                "certificate_url": f"/api/jobs/{job.id}/certificates/{r.id}"
                    if r.status == "succeeded" else None
            } for r in rows]
        })

    @app.get("/api/jobs/<job_id>/certificates/<recipient_id>")
    def download_certificate(job_id, recipient_id):
        recipient = Recipient.query.filter_by(id=recipient_id, job_id=job_id).first()
        if not recipient:
            return jsonify({"error": "Certificate recipient not found for this job."}), 404
        if recipient.status != "succeeded" or not recipient.certificate_path:
            return jsonify({"error": "Certificate is not available.",
                            "status": recipient.status, "detail": recipient.error}), 409
        if not Path(recipient.certificate_path).is_file():
            return jsonify({"error": "Certificate file is missing."}), 404
        return send_file(recipient.certificate_path, mimetype="application/pdf",
                         as_attachment=True, download_name=f"certificate-{recipient.name}.pdf")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
