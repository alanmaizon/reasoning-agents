# Demo Script — MDT (Misconception-Driven Tutor)

> Estimated time: ~90 seconds

## Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Running the Demo (Offline Mode)

```bash
python -m src.main --offline
```

## Step-by-Step Walkthrough

### 1. Launch (5s)
The CLI shows the MDT banner and prompts for focus topics.

```
┌──────────────────────────────────────────────┐
│ MDT — Misconception-Driven Tutor             │
│ AZ-900 Certification Prep • Powered by       │
│ Microsoft Foundry                             │
└──────────────────────────────────────────────┘

▶ 1/7  Student intake
Optional: Enter focus topics (comma-separated) or press Enter to skip:
> Security, Cloud Concepts
Optional: Daily study minutes (default 30):
> 20
```

### 2. Planning (5s)
PlannerAgent selects domains and question count.

```
▶ 2/7  Planning study session
  Domains: ['Cloud Concepts', 'Azure Architecture', 'Security']  |  Questions: 8
```

### 3. Quiz Generation (5s)
ExaminerAgent creates 8 multiple-choice questions.

```
▶ 3/7  Generating adaptive quiz
  Generated 8 questions
```

### 4. Quiz Time (30s)
Student answers each question interactively.

```
▶ 4/7  Quiz time!

Q1. Which cloud model allows organizations to share responsibility...
   1) Private cloud only
   2) Shared responsibility model
   3) On-premises model
   4) Hybrid DNS model
Your answer (number): 2
```

### 5. Diagnosis (10s)
MisconceptionAgent analyzes answers and identifies patterns.

```
▶ 5/7  Diagnosing misconceptions
┌────────────────────────────────────────┐
│           Diagnosis Summary            │
├──┬──────────┬──────────────┬───────────┤
│Q │ Correct? │ Misconception│ Why       │
├──┼──────────┼──────────────┼───────────┤
│1 │    ✅    │      —       │ Correct   │
│2 │    ❌    │   REGION     │ Confused  │
│...                                     │
└────────────────────────────────────────┘
Top misconceptions: REGION, SRM
```

### 6. Grounding (15s)
GroundingVerifierAgent attaches Microsoft Learn citations.

```
▶ 6/7  Grounding explanations with Microsoft Learn
╭─ Grounded Explanation ──────────────────╮
│ Q2                                      │
│ The correct answer is choice 2. ...     │
│                                         │
│   📎 [Azure regions and availability    │
│      zones](https://learn.microsoft...):│
│      Availability Zones are unique ...  │
╰─────────────────────────────────────────╯
```

### 7. Coaching (10s)
CoachAgent provides remediation and drills.

```
▶ 7/7  Generating coaching & micro-drills
📚 Coaching Notes
  • Review the shared responsibility model
  • Availability Zones provide HA within a single region

  Drill (REGION):
    → Explain the concept related to REGION in your own words.
    → Give a real-world example where REGION confusion could cause issues.

✅ Session complete. State saved.
```

## Expected Output Screenshots

> *[Screenshot placeholder: CLI banner]*
>
> *[Screenshot placeholder: Quiz interaction]*
>
> *[Screenshot placeholder: Diagnosis table]*
>
> *[Screenshot placeholder: Grounded explanations]*
>
> *[Screenshot placeholder: Coaching output]*

## Running with Foundry (Online Mode)

1. Copy `.env.example` to `.env` and fill in your Azure AI Foundry credentials
2. Run without `--offline`:
   ```bash
   python -m src.main
   ```
