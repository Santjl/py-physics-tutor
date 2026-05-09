
# Context for Extracting and Structuring AI-Generated Feedback

## General Objective

The system uses an AI model supported by RAG to analyze students’ answers in Physics questionnaires and generate personalized pedagogical feedback.

The purpose of this document is to help a coding agent understand what information should be extracted from the AI-generated feedback, how this information should be structured, and how it may be used by the application.

The feedback should not be treated only as free text. It contains different parts with different pedagogical functions, and these parts should be identified so the frontend can display them properly, the backend can store them in a structured format, and the results can later be analyzed.

---

## Pedagogical Context of the Feedback

The AI-generated feedback is intended to support the student after answering a question, especially when the answer is incorrect or partially correct.

The AI should act as a support tutor. It should not only say whether the student was right or wrong, but help the student understand:

- what the expected reasoning was;
- where the misunderstanding may have occurred;
- which Physics concept is involved;
- which part of the study material should be reviewed;
- what kind of guidance may help the student reorganize their understanding.

The feedback should be clear, educational, contextualized, and directly related to the question content.

---

## Main Information to Extract from the Feedback

Each AI-generated feedback should be interpreted as a structured object containing, at minimum, the following information.

---

## 1. Answer Evaluation Result

Identifies the general evaluation of the student’s answer.

Expected fields:

```json
{
  "status": "correct | incorrect | partially_correct",
  "summary": "Short summary of the evaluation of the student's answer."
}
````

Possible `status` values:

* `correct`: the answer is correct.
* `incorrect`: the answer is wrong.
* `partially_correct`: the answer contains some correct reasoning, but also has a conceptual error or is incomplete.

---

## 2. Explanation of the Correct Reasoning

This section explains the appropriate Physics reasoning needed to solve the question.

The explanation must be directly related to the question and should not be too generic.

Expected field:

```json
{
  "correct_reasoning": "Explanation of the correct reasoning for the question."
}
```

The coding agent should identify in the feedback:

* explanation of the Physics concept involved;
* relationship between physical quantities;
* justification of the correct alternative, when applicable;
* correction of the student’s reasoning path.

---

## 3. Possible Student Misconception

Identifies the possible difficulty shown by the student when answering the question.

Expected field:

```json
{
  "possible_misconception": "Description of the student's possible conceptual difficulty."
}
```

Examples of possible misconceptions:

* confusion between weight force and normal force;
* incorrect graph interpretation;
* wrong application of a formula;
* confusion between velocity and acceleration;
* mechanical use of equations without understanding the phenomenon;
* difficulty relating theoretical content to the problem situation.

Important: the AI should not state with absolute certainty that the student has a specific misconception. It should use careful language, such as:

* “The answer may suggest confusion between...”
* “The response indicates a possible difficulty with...”
* “The student may have interpreted...”

---

## 4. Related Physics Concepts

A list of Physics concepts or topics related to the question and to the student’s error.

Expected field:

```json
{
  "related_concepts": [
    "Concept 1",
    "Concept 2",
    "Concept 3"
  ]
}
```

Example:

```json
{
  "related_concepts": [
    "Newton's Laws",
    "Net Force",
    "Acceleration",
    "Weight Force"
  ]
}
```

This information may later be used to:

* generate difficulty reports by class;
* identify the most problematic topics;
* suggest review paths;
* organize pedagogical dashboards.

---

## 5. References to Study Materials

The system uses retrieved chunks from PDF study materials. Therefore, the feedback should indicate which pages or documents support the explanation.

Expected field:

```json
{
  "study_references": [
    {
      "file_name": "file-name.pdf",
      "page": 12,
      "reason": "Page recommended for reviewing the concept used in the question."
    }
  ]
}
```

These references must come from chunks retrieved by the RAG system.

The coding agent must ensure that the references displayed in the feedback correspond to documents and pages that were actually retrieved by the backend, preventing the AI from inventing pages or nonexistent sources.

---

## 6. Study Suggestion

A practical recommendation for the student to review the content.

Expected field:

```json
{
  "study_suggestion": "Objective study suggestion based on the identified difficulty."
}
```

Example:

```json
{
  "study_suggestion": "Review the concept of net force and observe how acceleration depends on the sum of the forces acting on the body."
}
```

The suggestion should be:

* short;
* clear;
* related to the student’s mistake;
* useful for guiding review.

---

## 7. Final Feedback Message to the Student

Final message written in a pedagogical and supportive tone.

Expected field:

```json
{
  "student_feedback": "Final message that will be displayed to the student."
}
```

This field may combine the main explanation in a student-friendly format.

It should avoid a punitive or overly technical tone. Ideally, the message should help the student understand the mistake and feel guided to try again.

---

## Expected Final Feedback Structure

The coding agent should convert the AI feedback into a structure similar to this:

```json
{
  "status": "incorrect",
  "summary": "The answer shows confusion between applied force and net force.",
  "correct_reasoning": "According to Newton's Second Law, the acceleration of a body depends on the net force acting on it and its mass. Therefore, it is not enough to consider only one isolated force; it is necessary to consider the sum of all forces involved.",
  "possible_misconception": "The answer may suggest that the student confused applied force with net force.",
  "related_concepts": [
    "Newton's Second Law",
    "Net Force",
    "Mass",
    "Acceleration"
  ],
  "study_references": [
    {
      "file_name": "newtons-laws.pdf",
      "page": 8,
      "reason": "Review the relationship between net force, mass, and acceleration."
    }
  ],
  "study_suggestion": "Review Newton's Second Law and observe examples where multiple forces act simultaneously on the same body.",
  "student_feedback": "Your answer shows a good attempt to relate force and motion, but it is important to remember that acceleration depends on the net force, not only on one isolated force. Review this point and try to identify all the forces acting on the body before applying the formula."
}
```

---

## Important Rules for the Coding Agent

### Do Not Treat the Feedback as a Single Text Block

The feedback should be divided into semantically useful parts. This allows the application to display each part in different sections, such as:

* Result;
* Explanation;
* Possible misconception;
* Where to review;
* Study suggestion.

---

### Do Not Invent References

References to study material must be based only on the chunks retrieved by the RAG mechanism.

If there is no reliable reference, the field should return an empty list:

```json
{
  "study_references": []
}
```

Or include a warning:

```json
{
  "study_references": [],
  "reference_warning": "No reliable reference was found in the retrieved study materials."
}
```

---

### Separate Internal Analysis from the Student-Facing Message

The system may store more analytical information, such as `possible_misconception`, but not all of it needs to be displayed directly to the student.

For example:

```json
{
  "possible_misconception": "The student seems to confuse velocity with acceleration."
}
```

Can be converted into a more careful message:

```text
It may be useful to review the difference between velocity and acceleration, since these concepts appear differently in the question.
```

---

## Possible Use in the Frontend

The frontend may display the feedback using cards or sections:

1. **Answer Result**
2. **Explanation**
3. **Point of Attention**
4. **Where to Review**
5. **Study Suggestion**

Example display:

```text
Result:
Your answer is partially correct.

Explanation:
The correct reasoning involves understanding that...

Point of attention:
There may have been confusion between...

Where to review:
Material: Newton's Laws.pdf — page 8

Suggestion:
Review the examples about net force before trying a similar question.
```

---

## Possible Use in the Database

The extracted structure may be stored in a table related to the student’s attempt.

Conceptual example:

```text
StudentAttemptFeedback
- Id
- StudentAttemptId
- Status
- Summary
- CorrectReasoning
- PossibleMisconception
- RelatedConceptsJson
- StudyReferencesJson
- StudySuggestion
- StudentFeedback
- CreatedAt
```

---

## Possible Use in Reports

With structured feedback, the system may generate qualitative and quantitative analyses, such as:

* which concepts appear most often in mistakes;
* which types of difficulties are most frequent;
* which questions cause more confusion;
* which materials are most recommended for review;
* how the student evolves across questionnaires.

This information may be used in the results section of the academic work, especially to discuss how the AI contributed to producing personalized feedback.

---

## Summary for the Coding Agent

The coding agent must understand that the AI-generated feedback is not only a textual message. It represents a structured pedagogical analysis of the student’s answer.

The extraction should identify:

* the result of the answer;
* the correct explanation;
* the possible conceptual difficulty;
* the Physics concepts involved;
* the references to retrieved materials;
* the study suggestion;
* the final message for the student.

The final structure should support storage, frontend display, and later educational data analysis.

