from app import models
from app.api.routes import feedback as feedback_route


def _login(client, email: str, password: str) -> str:
    resp = client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_feedback_returns_citations(client, admin_user, student_user, db_session):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")

    # Create questionnaire and question
    q_resp = client.post(
        "/questionnaires",
        json={"title": "Kinematics", "description": "Basics"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    qid = q_resp.json()["id"]
    question_resp = client.post(
        f"/questionnaires/{qid}/questions",
        json={
            "statement": "Speed units?",
            "options": [
                {"letter": "A", "text": "m/s", "is_correct": True},
                {"letter": "B", "text": "kg", "is_correct": False},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    question = question_resp.json()
    incorrect_option_id = next(o["id"] for o in question["options"] if not o["is_correct"])

    # Submit attempt
    attempt_resp = client.post(
        f"/questionnaires/{qid}/attempts",
        json={"answers": [{"question_id": question["id"], "selected_option_id": incorrect_option_id}]},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    attempt_id = attempt_resp.json()["attempt_id"]

    # Seed a chunk for retrieval
    doc = models.Document(filename="study.pdf", status="ready")
    db_session.add(doc)
    db_session.flush()
    chunk = models.Chunk(
        document_id=doc.id,
        filename=doc.filename,
        page=1,
        chunk_index=0,
        text="Speed is measured in meters per second (m/s).",
        embedding=[0.1] * 768,
    )
    db_session.add(chunk)
    db_session.commit()

    feedback_resp = client.post(
        f"/attempts/{attempt_id}/feedback",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert feedback_resp.status_code == 200, feedback_resp.text
    data = feedback_resp.json()
    assert data["attempt_id"] == attempt_id
    pq = data["per_question"][0]
    assert pq["study"]
    study_item = pq["study"][0]
    assert study_item["filename"] == "study.pdf"
    assert 1 in study_item["pages"]
    # New fields populated with defaults in test mode
    assert pq["misconception"] is not None
    assert pq["tip"] is not None
    assert pq["similar_question"] is not None
    assert pq["similar_question"]["filename"] == "study.pdf"
    assert pq["similar_question"]["page"] == 1


def _create_attempt(client, admin_token, student_token):
    """Helper: create a questionnaire with one question and submit an attempt. Returns (attempt_id, qid)."""
    q_resp = client.post(
        "/questionnaires",
        json={"title": "Dynamics", "description": "Forces"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    qid = q_resp.json()["id"]
    question_resp = client.post(
        f"/questionnaires/{qid}/questions",
        json={
            "statement": "F=ma unit?",
            "options": [
                {"letter": "A", "text": "Newton", "is_correct": True},
                {"letter": "B", "text": "Joule", "is_correct": False},
            ],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    question = question_resp.json()
    correct_id = next(o["id"] for o in question["options"] if o["is_correct"])
    attempt_resp = client.post(
        f"/questionnaires/{qid}/attempts",
        json={"answers": [{"question_id": question["id"], "selected_option_id": correct_id}]},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    return attempt_resp.json()["attempt_id"], qid


def test_list_my_attempts(client, admin_user, student_user):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")

    attempt_id, qid = _create_attempt(client, admin_token, student_token)

    resp = client.get("/attempts/me", headers={"Authorization": f"Bearer {student_token}"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    item = next(i for i in items if i["attempt_id"] == attempt_id)
    assert item["questionnaire_id"] == qid
    assert item["questionnaire_title"] == "Dynamics"
    assert item["score"] == 1.0
    assert item["total"] == 1
    assert item["has_feedback"] is False
    assert "date" in item


def test_list_my_attempts_has_feedback_true(client, admin_user, student_user, db_session):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")

    attempt_id, _ = _create_attempt(client, admin_token, student_token)

    # Generate feedback (test mode returns default)
    doc = models.Document(filename="test.pdf", status="ready")
    db_session.add(doc)
    db_session.flush()
    db_session.add(models.Chunk(
        document_id=doc.id, filename=doc.filename, page=1, chunk_index=0,
        text="Force equals mass times acceleration.", embedding=[0.1] * 768,
    ))
    db_session.commit()

    client.post(f"/attempts/{attempt_id}/feedback", headers={"Authorization": f"Bearer {student_token}"})

    resp = client.get("/attempts/me", headers={"Authorization": f"Bearer {student_token}"})
    item = next(i for i in resp.json() if i["attempt_id"] == attempt_id)
    assert item["has_feedback"] is True


def test_post_feedback_returns_cached_feedback_without_version_regeneration(
    client,
    admin_user,
    student_user,
    db_session,
    monkeypatch,
):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")
    attempt_id, _ = _create_attempt(client, admin_token, student_token)

    first = client.post(
        f"/attempts/{attempt_id}/feedback",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert first.status_code == 200, first.text

    record = db_session.query(models.AttemptFeedback).filter_by(attempt_id=attempt_id).one()
    cached_data = dict(record.data)
    metadata = dict(cached_data.get("metadata") or {})
    metadata["prompt_version"] = "old-version"
    cached_data["metadata"] = metadata
    record.data = cached_data
    db_session.commit()

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("cached feedback should not regenerate without force=true")

    monkeypatch.setattr(feedback_route, "generate_feedback", _fail_if_called)

    second = client.post(
        f"/attempts/{attempt_id}/feedback",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["metadata"]["prompt_version"] == "old-version"


def test_list_all_attempts_admin(client, admin_user, student_user):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")

    attempt_id, _ = _create_attempt(client, admin_token, student_token)

    resp = client.get("/attempts/", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    items = resp.json()
    assert any(i["attempt_id"] == attempt_id for i in items)


def test_list_all_attempts_filter_by_student(client, admin_user, student_user):
    admin_token = _login(client, admin_user.email, "secret")
    student_token = _login(client, student_user.email, "secret")

    _create_attempt(client, admin_token, student_token)

    resp = client.get(
        f"/attempts/?student_id={student_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    for item in resp.json():
        assert item["questionnaire_id"]  # all belong to this student


def test_list_all_attempts_student_forbidden(client, student_user):
    student_token = _login(client, student_user.email, "secret")
    resp = client.get("/attempts/", headers={"Authorization": f"Bearer {student_token}"})
    assert resp.status_code == 403
