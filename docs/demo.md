# Demo Script - Condor Console (Readiness Coach)

Estimated time: 2-3 minutes.

## Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Start the app (web demo)

```bash
uvicorn src.api:app --reload --port 8000
```

Open `http://127.0.0.1:8000/`.

## Demo flow

### 1. Session setup (15s)

In the Condor Console UI:
1. Choose `Session Mode`:
   - `Adaptive coaching` (typically 8-12 questions)
   - `Mock AZ-900 test` (randomized 40-60 questions)
2. (Adaptive only) Enter optional `Focus Topics`.
3. Click `Start Session`.

Expected:
- Plan metadata appears (`Mode`, `domains`, `question count`).
- Quiz cards render in accordion format.
- While an exam is active, `Start Session` becomes `Restart Session` and asks for confirmation before reset.

### 2. Quiz interaction (30-45s)

Answer a few questions.

Expected:
- Only one accordion question stays open at a time.
- Status chips update per question (`Answered` / `Not answered`).
- Progress line updates (example: `Answered: 6/48`).

### 3. Dropdown sentence-completion question (20s)

In mock mode, find a question containing `[Dropdown Menu]`.

Expected:
- The sentence shows a `<select>` dropdown embedded in the sentence.
- Selecting an option saves the answer and updates status/progress.

Example pattern:
- `An example of [Dropdown Menu] is ...`
- Options are concept terms (for example: Horizontal scaling, Vertical scaling, High availability, Low latency).

### 4. Submit and evaluate (25s)

Click `Submit Answers`.

Expected:
- Submit button shows loading spinner/state while processing.
- Exam panel is locked during submit.

Expected in `Evaluation + Insights`:
- `Evaluation Summary`:
  - Estimated score (`x/1000`)
  - Correct answers (`x/y`)
  - Accuracy (`%`)
  - Estimated result vs pass threshold (`700/1000`)
- Per-topic/domain score cards (`correct/total` + `%`)

Mode-specific result panels:
- `Mock AZ-900 test`: evaluation-only view (summary + topic scores)
- `Adaptive coaching`: collapsible sections:
  - `Answer Review` (larger readability-focused cards)
  - `Top Misconceptions`
  - `Lesson Points`
  - `Grounded Explanations` with Microsoft Learn citations

### 5. Optional: restart flow (15s)

Click `Restart Session` during an active exam.

Expected:
- Confirmation dialog appears before reset.
- Button shows loading state while the new session is generated.

## API sanity checks (optional)

```bash
curl -sS http://127.0.0.1:8000/healthz
```

Start adaptive:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/session/start \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","mode":"adaptive","focus_topics":["Security"],"offline":true}' | jq '{mode, q: (.exam.questions|length)}'
```

Start mock:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/session/start \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo","mode":"mock_test","offline":true}' | jq '{mode, q: (.exam.questions|length)}'
```

## Screenshot checklist

- Session setup with `Session Mode` selector
- Accordion question list with status chips
- Dropdown sentence-completion question
- Evaluation Summary panel with topic score cards
- Adaptive mode collapsible insight sections
- Grounded Explanations with citations
