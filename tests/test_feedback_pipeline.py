from types import SimpleNamespace
import time

from app import models
from app.rag.providers import RetrievedContextItem
from app.schemas import FeedbackResponse, PerQuestionFeedback, SimilarExercise, StudyItem
from app.rag import feedback as fb


def _make_chunk(
    chunk_id: int,
    filename: str,
    page: int,
    chapter_title: str | None = None,
    section_title: str | None = None,
    chunk_type: str = "unknown",
) -> models.Chunk:
    return models.Chunk(
        id=chunk_id,
        document_id=1,
        filename=filename,
        page=page,
        chunk_index=chunk_id,
        text="stub",
        embedding=[0.0] * 768,
        chapter_title=chapter_title,
        section_title=section_title,
        chunk_type=chunk_type,
    )


def _build_attempt() -> models.Attempt:
    q1 = models.Question(id=1, statement="Q1", questionnaire_id=1)
    q1a = models.Option(id=1, question_id=1, letter="A", text="wrong", is_correct=False)
    q1b = models.Option(id=2, question_id=1, letter="B", text="right", is_correct=True)
    q1.options = [q1a, q1b]

    q2 = models.Question(id=2, statement="Q2", questionnaire_id=1)
    q2a = models.Option(id=3, question_id=2, letter="A", text="wrong", is_correct=False)
    q2b = models.Option(id=4, question_id=2, letter="B", text="right", is_correct=True)
    q2.options = [q2a, q2b]

    a1 = models.Answer(
        id=1,
        attempt_id=1,
        question_id=1,
        selected_option_id=1,
        is_correct=False,
    )
    a1.option = q1a
    a1.question = q1

    a2 = models.Answer(
        id=2,
        attempt_id=1,
        question_id=2,
        selected_option_id=3,
        is_correct=False,
    )
    a2.option = q2a
    a2.question = q2

    attempt = models.Attempt(id=1, score=0.0, total=2)
    attempt.answers = [a1, a2]
    return attempt


def _build_attempt_with_questions(total_questions: int) -> models.Attempt:
    attempt = models.Attempt(id=1, score=0.0, total=total_questions)
    answers = []

    for idx in range(1, total_questions + 1):
        question = models.Question(id=idx, statement=f"Q{idx}", questionnaire_id=1)
        wrong = models.Option(id=(idx * 2) - 1, question_id=idx, letter="A", text="wrong", is_correct=False)
        right = models.Option(id=idx * 2, question_id=idx, letter="B", text="right", is_correct=True)
        question.options = [wrong, right]

        answer = models.Answer(
            id=idx,
            attempt_id=1,
            question_id=idx,
            selected_option_id=wrong.id,
            is_correct=False,
        )
        answer.option = wrong
        answer.question = question
        answers.append(answer)

    attempt.answers = answers
    return attempt


def test_extract_source_ids_and_mapping():
    chunk1 = _make_chunk(1, "book.pdf", 10)
    chunk2 = _make_chunk(2, "notes.pdf", 20)
    source_map = {"S1": chunk1, "S2": chunk2}

    text = "Veja (S2) e (S1) e (S2)."
    ids = fb.extract_source_ids(text, set(source_map.keys()))
    assert ids == ["S2", "S1"]

    chunks = fb.map_source_ids_to_chunks(ids, source_map)
    assert [(c.filename, c.page) for c in chunks] == [("notes.pdf", 20), ("book.pdf", 10)]


def test_extract_source_ids_ignores_unknown():
    chunk1 = _make_chunk(1, "book.pdf", 10)
    source_map = {"S1": chunk1}
    ids = fb.extract_source_ids("Nada (S9) e (S1).", set(source_map.keys()))
    assert ids == ["S1"]


def test_per_question_fallback_does_not_break_response():
    class FakeLLM:
        def invoke(self, messages):
            user_prompt = messages[-1].content
            if "Q1:" in user_prompt:
                raise RuntimeError("boom")
            return SimpleNamespace(
                content=(
                    "Raciocinio simulado:\nPensei que A era certo porque confundi\n\n"
                    "Explicacao:\nTexto (S1)\n\n"
                    "Onde estudar no livro:\n- Cinematica (S1)\n\n"
                    "Exercicio similar:\nVeja o exercicio em (E1). Pratica de MRU.\n\n"
                    "Dica:\nRevise as unidades de medida."
                )
            )

    attempt = _build_attempt()
    per_q_chunks = {
        1: [_make_chunk(1, "a.pdf", 1)],
        2: [_make_chunk(2, "b.pdf", 2)],
    }
    ex_chunk = _make_chunk(10, "b.pdf", 5, chunk_type="exercise")
    per_q_exercises = {
        1: [],
        2: [ex_chunk],
    }
    response = fb._generate_feedback_with_llm(FakeLLM(), attempt, per_q_chunks, per_q_exercises)

    assert len(response.per_question) == 2
    pq1 = next(pq for pq in response.per_question if pq.question_id == 1)
    pq2 = next(pq for pq in response.per_question if pq.question_id == 2)
    # pq1 fell back to default
    assert "Revise o conceito" in pq1.explanation
    assert pq1.misconception is not None
    # pq2 was parsed from LLM sections
    assert "Texto" in pq2.explanation
    assert "Explicacao:" not in pq2.explanation
    assert pq2.misconception is not None
    assert "confundi" in pq2.misconception
    assert pq2.similar_question is not None
    assert pq2.similar_question.filename == "b.pdf"
    assert pq2.similar_question.page == 5
    assert pq2.tip is not None


def test_summary_is_computed_in_code():
    attempt = models.Attempt(id=1, score=1.0, total=3)
    summary = fb._build_summary(attempt)
    assert summary.score == 1.0
    assert summary.total == 3
    assert summary.strengths == ["Respondeu corretamente"]
    assert summary.weaknesses == ["Questoes com erro"]


def test_study_grouping_unique_sorted_pages():
    chunk1 = _make_chunk(1, "a.pdf", 3, chapter_title="Capitulo 1")
    chunk2 = _make_chunk(2, "a.pdf", 1, chapter_title="Capitulo 1")
    chunk3 = _make_chunk(3, "a.pdf", 3, chapter_title="Capitulo 2")
    study = fb._build_study_groups([chunk1, chunk2, chunk3])
    assert any(item.pages == [1, 3] and item.chapter == "Capitulo 1" for item in study)
    assert any(item.pages == [3] and item.chapter == "Capitulo 2" for item in study)


def test_study_grouping_keeps_distinct_sections_separate():
    chunk1 = _make_chunk(1, "a.pdf", 17, chapter_title="Capitulo 2", section_title="Forca resultante")
    chunk2 = _make_chunk(2, "a.pdf", 78, chapter_title="Capitulo 2", section_title="Centro de massa")

    study = fb._build_study_groups([chunk1, chunk2])

    assert len(study) == 2
    assert any(item.chapter == "Capitulo 2 / Forca resultante" and item.pages == [17] for item in study)
    assert any(item.chapter == "Capitulo 2 / Centro de massa" and item.pages == [78] for item in study)


def test_feedback_response_model_validate_passes():
    pq = PerQuestionFeedback(
        question_id=1,
        is_correct=False,
        explanation="Texto",
        misconception="Confundiu A com B",
        tip="Releia o enunciado",
        similar_question=SimilarExercise(filename="a.pdf", page=3, description="Exercicio de cinematica"),
        study=[StudyItem(filename="a.pdf", pages=[1], topic="Cinematica")],
    )
    response = FeedbackResponse(
        attempt_id=1,
        summary=fb._build_summary(models.Attempt(id=1, score=0.0, total=1)),
        per_question=[pq],
        global_references=[],
    )
    validated = FeedbackResponse.model_validate(response.model_dump())
    assert validated.attempt_id == 1
    assert validated.per_question[0].misconception == "Confundiu A com B"
    assert validated.per_question[0].tip == "Releia o enunciado"
    assert validated.per_question[0].similar_question.filename == "a.pdf"
    assert validated.per_question[0].similar_question.page == 3
    assert validated.per_question[0].study[0].topic == "Cinematica"


def test_parse_llm_sections():
    text = (
        "Raciocinio simulado:\nPasso 1, passo 2, erro aqui\n\n"
        "Explicacao:\nO correto e fazer X porque Y (S1)\n\n"
        "Onde estudar no livro:\n- Leis de Newton (S1)\n\n"
        "Exercicio similar:\nVeja o exercicio em (E1). Pratica de dinamica.\n\n"
        "Dica:\nSempre desenhe o diagrama de corpo livre."
    )
    sections = fb._parse_llm_sections(text)
    assert "raciocinio simulado" in sections
    assert "Passo 1" in sections["raciocinio simulado"]
    assert "explicacao" in sections
    assert "O correto" in sections["explicacao"]
    assert "onde estudar no livro" in sections
    assert "Leis de Newton" in sections["onde estudar no livro"]
    assert "exercicio similar" in sections
    assert "E1" in sections["exercicio similar"]
    assert "dica" in sections
    assert "diagrama" in sections["dica"]


def test_parse_llm_sections_legacy_format():
    text = (
        "Explicacao:\nO correto e X\n\n"
        "Possivel confusao:\nConfundiu forca com energia\n\n"
        "Onde estudar no livro:\n- Energia (S1)"
    )
    sections = fb._parse_llm_sections(text)
    assert "possivel confusao" in sections
    assert "Confundiu" in sections["possivel confusao"]
    assert "explicacao" in sections


def test_study_item_topic_from_build_study_groups():
    chunk = _make_chunk(1, "book.pdf", 5, chapter_title="Cap 3")
    study = fb._build_study_groups([chunk], topic_text="- Leis de Newton (S1)")
    assert len(study) == 1
    assert study[0].topic == "Leis de Newton"


def test_study_item_reason_is_preserved():
    chunk = _make_chunk(1, "book.pdf", 5, chapter_title="Cap 3")
    study = fb._build_study_groups([chunk], topic_text="- Leis de Newton (S1)", reason="Revise a 2a lei.")
    assert len(study) == 1
    assert study[0].reason == "Revise a 2a lei."


def test_sanitize_per_question_feedback_appends_study_location():
    pq = PerQuestionFeedback(
        question_id=1,
        is_correct=False,
        explanation="Texto",
        student_feedback="Revise este conceito.",
        study_suggestion="Retome o material.",
        study=[StudyItem(filename="halliday.pdf", pages=[23, 24], chapter="Capitulo 2", topic="Aceleracao")],
    )

    sanitized = fb._sanitize_per_question_feedback(pq)

    assert sanitized.student_feedback is not None
    assert "Onde estudar:" in sanitized.student_feedback
    assert "halliday.pdf" in sanitized.student_feedback
    assert "23, 24" in sanitized.student_feedback
    assert sanitized.study_suggestion is not None
    assert "halliday.pdf" in sanitized.study_suggestion


def test_default_feedback_populates_new_fields():
    attempt = _build_attempt()
    ans = attempt.answers[0]
    chunks = [_make_chunk(1, "a.pdf", 1)]
    pq = fb._default_per_question_feedback(ans, chunks)
    assert pq.misconception is not None
    assert "raciocinio" in pq.misconception.lower()
    assert pq.tip is not None
    assert "enunciado" in pq.tip.lower()
    assert pq.similar_question is not None
    assert pq.similar_question.filename == "a.pdf"
    assert pq.similar_question.page == 1
    sanitized = fb._sanitize_per_question_feedback(pq)
    assert sanitized.study_suggestion is not None
    assert "a.pdf" in sanitized.study_suggestion


def test_collect_global_references_uses_chunk_snippets():
    chunk = _make_chunk(1, "book.pdf", 5)
    chunk.text = "A aceleracao e a taxa de variacao da velocidade no tempo."
    per_question = [
        PerQuestionFeedback(
            question_id=1,
            is_correct=False,
            explanation="Texto",
            study=[StudyItem(filename="book.pdf", pages=[5], topic="Cinematica")],
        )
    ]

    refs = fb._collect_global_references(per_question, {1: [chunk]})

    assert len(refs) == 1
    assert refs[0].filename == "book.pdf"
    assert refs[0].page == 5
    assert "taxa de variacao da velocidade" in refs[0].snippet


def test_google_item_to_chunk_maps_core_fields():
    item = RetrievedContextItem(
        id="doc-1",
        title="fisica.pdf",
        source_uri="gs://bucket/fisica.pdf",
        page_number=10,
        snippet="Secao 25: movimento uniforme.",
        metadata={
            "struct_data": {"chapter_title": "Capitulo 2", "section_title": "Secao 25"},
            "derived_struct_data": {},
        },
        rank=1,
    )

    chunk = fb._google_item_to_chunk(item, chunk_index=1)

    assert chunk.filename == "fisica.pdf"
    assert chunk.page == 10
    assert chunk.text == "Secao 25: movimento uniforme."
    assert chunk.chapter_title == "Capitulo 2"
    assert chunk.section_title == "Secao 25"
    assert chunk.chunk_type == "theory"


def test_retrieve_per_question_uses_google_provider(monkeypatch):
    attempt = _build_attempt()

    class FakeGoogleAgentSearchService:
        def search_relevant_context(self, query, page_size=None):
            assert "Questao:" in query
            return [
                RetrievedContextItem(
                    id="doc-1",
                    title="fisica.pdf",
                    source_uri="gs://bucket/fisica.pdf",
                    page_number=10,
                    snippet="Trecho relevante sobre velocidade.",
                    metadata={},
                    rank=1,
                )
            ]

    monkeypatch.setattr(
        fb,
        "get_settings",
        lambda: SimpleNamespace(
            retrieval_provider="google",
            max_concurrent_retrievals=2,
        ),
    )
    monkeypatch.setattr(fb, "GoogleAgentSearchService", FakeGoogleAgentSearchService)

    per_q_chunks, per_q_exercises = fb._retrieve_per_question(None, attempt, top_k=4, exercise_top_k=2)

    assert set(per_q_chunks) == {1, 2}
    assert per_q_chunks[1][0].filename == "fisica.pdf"
    assert per_q_chunks[1][0].page == 10
    assert per_q_chunks[1][0].text == "Trecho relevante sobre velocidade."
    assert per_q_exercises[1] == []


def test_summary_perfect_score():
    attempt = models.Attempt(id=1, score=5.0, total=5)
    summary = fb._build_summary(attempt)
    assert summary.strengths == ["Respondeu corretamente"]
    assert summary.weaknesses == []


def test_summary_zero_score():
    attempt = models.Attempt(id=1, score=0.0, total=5)
    summary = fb._build_summary(attempt)
    assert summary.strengths == []
    assert summary.weaknesses == ["Questoes com erro"]


def test_correct_answers_are_excluded_from_feedback():
    """Only incorrect answers should produce per-question feedback."""
    attempt = _build_attempt()
    # Mark first answer as correct
    attempt.answers[0].is_correct = True
    attempt.score = 1.0

    chunks = [_make_chunk(1, "a.pdf", 1)]
    per_q_chunks = {
        1: chunks,
        2: chunks,
    }

    class FakeLLM:
        def invoke(self, messages):
            return SimpleNamespace(
                content=(
                    "Explicacao:\nTexto\n\n"
                    "Dica:\nRevise."
                )
            )

    response = fb._generate_feedback_with_llm(FakeLLM(), attempt, per_q_chunks)
    assert len(response.per_question) == 1
    assert response.per_question[0].question_id == 2


def test_sanitize_truncates_long_explanation():
    long_text = "A" * 2000
    pq = PerQuestionFeedback(
        question_id=1,
        is_correct=False,
        explanation=long_text,
        misconception="B" * 800,
        tip="C" * 500,
    )
    sanitized = fb._sanitize_per_question_feedback(pq)
    assert len(sanitized.explanation) <= fb.EXPLANATION_MAX_CHARS
    assert len(sanitized.misconception) <= fb.MISCONCEPTION_MAX_CHARS
    assert len(sanitized.tip) <= fb.TIP_MAX_CHARS


def test_extract_similar_exercise_from_exercise_map():
    ex_chunk = _make_chunk(1, "ex.pdf", 7, chunk_type="exercise")
    sections = {"exercicio similar": "Veja o exercicio em (E1). Otimo para treinar."}
    exercise_map = {"E1": ex_chunk}
    result = fb._extract_similar_exercise(
        sections, exercise_map, [ex_chunk],
    )
    assert result is not None
    assert result.filename == "ex.pdf"
    assert result.page == 7


def test_extract_similar_exercise_fallback_to_theory():
    theory_chunk = _make_chunk(1, "book.pdf", 10)
    sections = {"exercicio similar": "Sem exercicios, veja (S1)."}
    source_map = {"S1": theory_chunk}
    result = fb._extract_similar_exercise(
        sections, exercise_map={}, exercise_chunks=[],
        theory_chunks=[theory_chunk], source_map=source_map,
    )
    assert result is not None
    assert result.filename == "book.pdf"
    assert result.page == 10


def test_default_per_question_no_chunks():
    attempt = _build_attempt()
    ans = attempt.answers[0]
    pq = fb._default_per_question_feedback(ans, chunks=[])
    assert "Revise o conceito" in pq.explanation
    assert pq.similar_question is None
    assert pq.study == []


def test_feedback_timeout_does_not_count_queue_wait(monkeypatch):
    attempt = _build_attempt_with_questions(5)
    per_q_chunks = {
        ans.question_id: [_make_chunk(ans.question_id, f"{ans.question_id}.pdf", ans.question_id)]
        for ans in attempt.answers
    }

    class FakeLLM:
        def invoke(self, messages):
            user_prompt = messages[-1].content
            question_number = int(user_prompt.split("Questao ")[1].split("\n", 1)[0])
            if question_number in {1, 2}:
                time.sleep(0.8)
            elif question_number in {3, 4}:
                time.sleep(0.4)
            return SimpleNamespace(
                content=(
                    "Explicacao:\nTexto (S1)\n\n"
                    "Dica:\nRevise.\n\n"
                    "Mensagem para o aluno:\nTudo certo."
                )
            )

    monkeypatch.setattr(fb, "build_system_prompt_per_question", lambda: "system")

    def fake_build_user_prompt(ans, chunks, exercise_chunks):
        return (f"Questao {ans.question_id}\n", {"S1": chunks[0]} if chunks else {}, {})

    monkeypatch.setattr(fb, "build_user_prompt_for_question", fake_build_user_prompt)
    monkeypatch.setattr(
        fb,
        "get_settings",
        lambda: SimpleNamespace(
            feedback_enable_relevance_filter=False,
            feedback_max_regeneration_attempts=0,
            feedback_enable_validator=False,
            max_concurrent_llm_calls=2,
            question_feedback_timeout_seconds=1,
            max_concurrent_validations=1,
            max_quota_errors_per_attempt=3,
        ),
    )

    response = fb._generate_feedback_with_llm(FakeLLM(), attempt, per_q_chunks)

    timed_out_ids = {err.question_id for err in response.errors if err.type == "timeout"}
    assert timed_out_ids == set()
    assert {pq.question_id for pq in response.per_question} == {1, 2, 3, 4, 5}


def test_feedback_quota_circuit_skips_later_questions(monkeypatch):
    attempt = _build_attempt_with_questions(4)
    per_q_chunks = {
        ans.question_id: [_make_chunk(ans.question_id, f"{ans.question_id}.pdf", ans.question_id)]
        for ans in attempt.answers
    }
    invoked_questions = []

    class FakeLLM:
        def invoke(self, messages):
            user_prompt = messages[-1].content
            question_number = int(user_prompt.split("Questao ")[1].split("\n", 1)[0])
            invoked_questions.append(question_number)
            if question_number == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return SimpleNamespace(
                content=(
                    "Explicacao:\nTexto (S1)\n\n"
                    "Dica:\nRevise.\n\n"
                    "Mensagem para o aluno:\nTudo certo."
                )
            )

    monkeypatch.setattr(fb, "build_system_prompt_per_question", lambda: "system")

    def fake_build_user_prompt(ans, chunks, exercise_chunks):
        return (f"Questao {ans.question_id}\n", {"S1": chunks[0]} if chunks else {}, {})

    monkeypatch.setattr(fb, "build_user_prompt_for_question", fake_build_user_prompt)
    monkeypatch.setattr(
        fb,
        "get_settings",
        lambda: SimpleNamespace(
            feedback_enable_relevance_filter=False,
            feedback_max_regeneration_attempts=0,
            feedback_enable_validator=False,
            max_concurrent_llm_calls=1,
            question_feedback_timeout_seconds=5,
            max_concurrent_validations=1,
            max_quota_errors_per_attempt=1,
        ),
    )

    response = fb._generate_feedback_with_llm(FakeLLM(), attempt, per_q_chunks)

    assert invoked_questions == [1]
    assert len(response.per_question) == 4
    assert response.metadata is not None
    assert response.metadata.quota_error_count == 1
    assert all(
        pq.student_feedback and "limite temporario" in pq.student_feedback.lower()
        for pq in response.per_question
    )
