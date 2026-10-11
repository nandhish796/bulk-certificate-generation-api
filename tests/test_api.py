import os
import pytest
from app import create_app, db, render_certificate


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "test.db"),
        "CERTIFICATE_DIR": str(tmp_path / "certificates"),
        "PROCESS_INLINE": True,
    })
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def sample_payload():
    return {
        "event_name": "Python Bootcamp",
        "course_name": "Python Programming",
        "issue_date": "2026-10-11",
        "recipients": [
            {"name": "Asha Kumar", "email": "asha@example.com"},
            {"name": "Ravi Kumar", "email": "ravi@example.com"},
        ],
    }


def test_create_generation_job(client):
    response = client.post("/api/jobs", json=sample_payload())
    assert response.status_code == 202
    body = response.get_json()
    assert body["job_id"]
    assert body["total"] == 2
    assert body["status"] == "completed"  # test mode processes inline


def test_input_validation(client):
    response = client.post("/api/jobs", json={"event_name": "Event"})
    assert response.status_code == 400
    assert "recipients" in response.get_json()["error"]


def test_invalid_recipient_does_not_block_valid_recipient(client):
    payload = sample_payload()
    payload["recipients"] = [
        {"name": "", "email": "wrong"},
        {"name": "Asha Kumar", "email": "asha@example.com"},
    ]
    created = client.post("/api/jobs", json=payload)
    job_id = created.get_json()["job_id"]
    result = client.get(f"/api/jobs/{job_id}").get_json()
    assert result["status"] == "partial_failed"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    statuses = [r["status"] for r in result["recipients"]]
    assert "succeeded" in statuses and "failed" in statuses


def test_certificate_generation_creates_pdf(tmp_path):
    output = tmp_path / "sample.pdf"
    render_certificate(output, "Test Recipient", "Testing 101", "Demo Event", "2026-10-11")
    assert output.exists()
    assert output.stat().st_size > 500


def test_job_status_and_progress(client):
    response = client.post("/api/jobs", json=sample_payload())
    job_id = response.get_json()["job_id"]
    status = client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    body = status.get_json()
    assert body["total"] == 2
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert body["status"] == "completed"


def test_retrieve_generated_certificate(client):
    response = client.post("/api/jobs", json=sample_payload())
    job_id = response.get_json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").get_json()
    recipient_id = next(r["recipient_id"] for r in body["recipients"] if r["status"] == "succeeded")
    download = client.get(f"/api/jobs/{job_id}/certificates/{recipient_id}")
    assert download.status_code == 200
    assert download.mimetype == "application/pdf"
    assert download.data.startswith(b"%PDF")


def test_individual_generation_failure_is_isolated(app, client, monkeypatch):
    import app as app_module
    original = app_module.render_certificate

    def sometimes_fail(path, recipient_name, course_name, event_name, issue_date):
        if recipient_name == "Ravi Kumar":
            raise RuntimeError("simulated failure")
        return original(path, recipient_name, course_name, event_name, issue_date)

    monkeypatch.setattr(app_module, "render_certificate", sometimes_fail)
    response = client.post("/api/jobs", json=sample_payload())
    job_id = response.get_json()["job_id"]
    result = client.get(f"/api/jobs/{job_id}").get_json()
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["status"] == "partial_failed"
