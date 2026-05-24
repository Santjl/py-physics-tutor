import fitz

from app import models


def _login(client, email: str, password: str) -> str:
    resp = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_upload_pdf_processes_chunks(client, admin_user, db_session):
    token = _login(client, admin_user.email, "secret")
    pdf_bytes = make_pdf_bytes("Gravity on Earth is approximately 9.8 m/s^2.")

    resp = client.post(
        "/documents/upload",
        files={"file": ("gravity.pdf", pdf_bytes, "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    document_id = resp.json()["id"]
    assert resp.json()["status"] == "ready"

    status_resp = client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "ready"

    chunks = db_session.query(models.Chunk).filter_by(document_id=document_id).all()
    assert len(chunks) >= 1
    assert chunks[0].filename == "gravity.pdf"


def test_list_documents(client, admin_user, db_session):
    token = _login(client, admin_user.email, "secret")
    pdf_bytes = make_pdf_bytes("Kinematics: velocity and acceleration.")

    # Upload a document first
    upload_resp = client.post(
        "/documents/upload",
        files={"file": ("kinematics.pdf", pdf_bytes, "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload_resp.status_code == 201, upload_resp.text

    # List documents
    resp = client.get("/documents/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    filenames = [d["filename"] for d in data]
    assert "kinematics.pdf" in filenames


def test_delete_document(client, admin_user, db_session):
    token = _login(client, admin_user.email, "secret")
    pdf_bytes = make_pdf_bytes("Thermodynamics: heat and energy transfer.")

    upload_resp = client.post(
        "/documents/upload",
        files={"file": ("thermo.pdf", pdf_bytes, "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload_resp.status_code == 201
    doc_id = upload_resp.json()["id"]

    # Verify chunks exist
    chunks = db_session.query(models.Chunk).filter_by(document_id=doc_id).all()
    assert len(chunks) >= 1

    # Delete document
    resp = client.delete(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204

    # Verify document is gone
    get_resp = client.get(f"/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 404

    # Verify chunks are cascade-deleted
    db_session.expire_all()
    chunks = db_session.query(models.Chunk).filter_by(document_id=doc_id).all()
    assert len(chunks) == 0


def test_delete_document_not_found(client, admin_user):
    token = _login(client, admin_user.email, "secret")
    resp = client.delete("/documents/99999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_cancel_ready_document_rejected(client, admin_user):
    """In test mode, upload goes straight to 'ready' — cancel should be rejected."""
    token = _login(client, admin_user.email, "secret")
    pdf_bytes = make_pdf_bytes("Optics: reflection and refraction.")

    upload_resp = client.post(
        "/documents/upload",
        files={"file": ("optics.pdf", pdf_bytes, "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload_resp.status_code == 201
    doc_id = upload_resp.json()["id"]
    assert upload_resp.json()["status"] == "ready"

    resp = client.patch(f"/documents/{doc_id}/cancel", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 409


def test_cancel_pending_document(client, admin_user, db_session):
    """Manually create a pending document and cancel it."""
    token = _login(client, admin_user.email, "secret")

    doc = models.Document(filename="waves.pdf", status="pending")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    resp = client.patch(f"/documents/{doc.id}/cancel", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
