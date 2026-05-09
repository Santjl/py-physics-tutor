# Plan: Feedback Prompts & Flow — Context Doc Alignment

## TL;DR
The `feedback.context.md` defines 7 structured fields that AI feedback should produce. The current implementation covers only 2 fully (`explanation`/`correct_reasoning`, `misconception`). Missing: `status` (partially_correct), `evaluation_summary`, `related_concepts`, `study_suggestion`, `student_feedback`, and a `reason` on study references. The plan adds them across 6 sequential phases, all backward-compatible.

**User decisions:**
- `partially_correct` status: YES — LLM assesses reasoning quality, can output `partially_correct` even for multiple-choice
- Field naming: Keep `explanation`, add `correct_reasoning` as extra optional alias

---

## Phase 1 — Constants (`feedback_constants.py`)
Add 3 new char-limit constants:
- `STUDENT_FEEDBACK_MAX_CHARS = 800`
- `STUDY_SUGGESTION_MAX_CHARS = 600`
- `EVALUATION_SUMMARY_MAX_CHARS = 400`

---

## Phase 2 — Schema (`schemas.py`) *(additive only)*

**`StudyItem`** — add:
- `reason: Optional[str] = None` — why this source is recommended

**`PerQuestionFeedback`** — add:
- `status: Literal["correct","incorrect","partially_correct"] = "incorrect"` — LLM-assessed reasoning quality
- `correct_reasoning: Optional[str] = None` — alias of `explanation` per context doc naming
- `evaluation_summary: Optional[str] = None` — 1-2 sentence answer evaluation
- `related_concepts: List[str] = Field(default_factory=list)` — Physics topics involved
- `study_suggestion: Optional[str] = None` — specific "read/review X" action (distinct from `tip`)
- `student_feedback: Optional[str] = None` — supportive, student-friendly final message

**`FeedbackResponse`** — add:
- `related_concepts: List[str] = Field(default_factory=list)` — deduplicated across all questions

---

## Phase 3 — Prompts (`feedback_prompts.py`)

Update `build_system_prompt_per_question()`:
1. Add 4 new mandatory output sections to the format block:
   - `Avaliacao:` — output `correto` / `incorreto` / `parcialmente correto` + 1-2 sentence assessment of the student's reasoning quality
   - `Conceitos relacionados:` — comma-separated Physics concepts, max 5 items (e.g., "Segunda Lei de Newton, Forca Resultante, Aceleracao")
   - `Sugestao de estudo:` — what to read/review *now* (distinct from `Dica`, which is about avoiding future mistakes)
   - `Mensagem para o aluno:` — supportive, non-punitive, plain-language final message; should not repeat the explanation verbatim
2. Update `Onde estudar no livro:` instruction: ask for a short reason per bullet (e.g., `- <topico: motivo pelo qual o aluno deve rever> (S1)`)
3. Add new conciseness limits to REQUISITOS DE CONCISAO:
   - `Avaliacao (resumo) <= EVALUATION_SUMMARY_MAX_CHARS caracteres`
   - `Sugestao de estudo <= STUDY_SUGGESTION_MAX_CHARS caracteres`
   - `Mensagem para o aluno <= STUDENT_FEEDBACK_MAX_CHARS caracteres`
   - `Conceitos relacionados: no maximo 5 itens`
4. Update `Erro conceitual` hedging language: use "A resposta pode sugerir confusao entre..." not absolute assertions

Update `build_user_prompt_for_question()`:
- Append first `CHUNK_TEXT_MAX_CHARS` chars of each chunk's `.text` content into the source lines so the LLM has enough context to produce a meaningful `reason` for study references

---

## Phase 4 — Parser (`feedback_parser.py`)

Extend `_SECTION_HEADERS`: add:
- `"avaliacao"`
- `"conceitos relacionados"`
- `"sugestao de estudo"`
- `"mensagem para o aluno"`

Add 5 new extractor functions:

**`_extract_status(sections, is_correct: bool) -> Literal["correct","incorrect","partially_correct"]`**
- Reads `"avaliacao"` section first line; maps:
  - `"parcialmente correto"` → `"partially_correct"`
  - `"correto"` → `"correct"`
  - else: `"correct"` if `is_correct` else `"incorrect"`

**`_extract_evaluation_summary(sections) -> str | None`**
- Content of `"avaliacao"` section after the status keyword (subsequent lines or everything after the first word)

**`_extract_related_concepts(sections) -> list[str]`**
- Parses `"conceitos relacionados"` section; splits by comma and/or newline bullet, strips whitespace and leading `"- "`, caps at 8 items, returns clean list

**`_extract_study_suggestion(sections) -> str | None`**
- Returns content of `"sugestao de estudo"` section

**`_extract_student_feedback(sections) -> str | None`**
- Returns content of `"mensagem para o aluno"` section

---

## Phase 5 — Builder (`feedback_builder.py`)

1. **`_build_study_groups(chunks, topic_text, reason=None)`** — add `reason: str | None = None` param, pass it into `StudyItem(reason=reason)`
2. **`_sanitize_per_question_feedback()`** — truncate new text fields using new constants:
   - `correct_reasoning` → same limit as `explanation` (`EXPLANATION_MAX_CHARS`)
   - `evaluation_summary` → `EVALUATION_SUMMARY_MAX_CHARS`
   - `study_suggestion` → `STUDY_SUGGESTION_MAX_CHARS`
   - `student_feedback` → `STUDENT_FEEDBACK_MAX_CHARS`
   - `related_concepts` list → cap at 8 items, each item stripped of whitespace
3. **`_default_per_question_feedback()`** — populate new fields with PT-BR fallbacks:
   - `status`: `"correct"` if `ans.is_correct` else `"incorrect"` (no `partially_correct` in fallback path)
   - `correct_reasoning`: same value as `explanation`
   - `evaluation_summary`: `None` (omit in fallback)
   - `related_concepts`: `[]`
   - `study_suggestion`: generic suggestion based on chunks (e.g., "Revise o conteudo indicado nas fontes abaixo.")
   - `student_feedback`: `None`
4. **Add `_collect_global_concepts(per_question: Sequence[PerQuestionFeedback]) -> list[str]`** — deduplicated merge of `related_concepts` across all per-question items, preserving insertion order, capped at 15 items
5. **Fix `_build_summary()`** — replace English strings with PT-BR:
   - `"Answered correctly"` → `"Respondeu corretamente"`
   - `"Missed questions"` → `"Questoes com erro"`

---

## Phase 6 — Orchestrator (`feedback.py`)

**In `_process_one_question()`:**
1. After `_parse_llm_sections`, call:
   - `_extract_status(sections, ans.is_correct)`
   - `_extract_evaluation_summary(sections)`
   - `_extract_related_concepts(sections)`
   - `_extract_study_suggestion(sections)`
   - `_extract_student_feedback(sections)`
2. Pass `study_text` as `reason` to `_build_study_groups`
3. Build `PerQuestionFeedback` with all new fields:
   - Set both `explanation` and `correct_reasoning` from the same extracted `explanation` value
   - Include `status`, `evaluation_summary`, `related_concepts`, `study_suggestion`, `student_feedback`

**In `_generate_feedback_with_llm()`:**
1. After collecting all per-question results, call `_collect_global_concepts(per_question)`
2. Include in `FeedbackResponse(... related_concepts=global_concepts)`

**In `_default_feedback_from_per_q()`:**
1. Pass `related_concepts=[]` in `FeedbackResponse`

---

## Relevant Files
- `app/rag/feedback_constants.py` — Phase 1
- `app/schemas.py` — Phase 2
- `app/rag/feedback_prompts.py` — Phase 3
- `app/rag/feedback_parser.py` — Phase 4
- `app/rag/feedback_builder.py` — Phase 5
- `app/rag/feedback.py` — Phase 6
- `tests/test_feedback_pipeline.py` — verify existing tests still pass (new fields are all Optional/have defaults)

---

## Verification
1. `pytest` after Phase 2 — all 36 tests pass with new optional schema fields
2. `pytest` after Phase 6 — full suite green
3. Inspect `FeedbackResponse` JSON output to confirm all 7 context doc fields are present
4. Confirm `AttemptFeedback.data` in DB serializes new fields automatically (no migration needed — column is JSON)
5. Manual/test: confirm `status="partially_correct"` appears when LLM outputs "parcialmente correto"

---

## Decisions
- No DB migration required — `AttemptFeedback.data` is a JSON column; new fields serialize automatically
- `partially_correct` is LLM-assessed based on reasoning quality, orthogonal to `is_correct` (option-level correctness)
- `study_suggestion` = read/review X now; `tip` = avoid this mistake in the future (prompt must clearly differentiate)
- All new `PerQuestionFeedback` fields are `Optional` or have `default_factory` — no breaking schema change

---

## Further Considerations
1. **Chunk text in user prompt (Phase 3)**: Adding snippet text to source lines increases prompt size and LLM latency. If latency is a concern, keep source lines as metadata-only (current behavior) and skip the snippet addition.
2. **`study_suggestion` vs `tip` overlap**: Without precise wording in the prompt, the LLM may produce redundant content in both sections. The system prompt must make the distinction very explicit.
