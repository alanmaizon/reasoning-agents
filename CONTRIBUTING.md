# Contributing to Condor Console

Thanks for contributing.

This document focuses on practical contribution workflow for this repository.
For product/demo context, see `README.md` and `docs/demo.md`.

## What to Contribute

- Bug fixes (API, auth, quiz logic, UI/UX)
- Reliability and correctness improvements
- Test coverage for regressions
- Documentation improvements
- Deployment/operations scripts (optional, cloud-hosted scenarios)

## Local Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

If your shell maps commands differently, use `python3` and `pip3`.

## Run Locally

Offline CLI mode:

```bash
python -m src.main --offline
```

Local API + web UI:

```bash
uvicorn src.api:app --reload --port 8000
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/healthz`

## Testing Requirements

Run before opening a PR:

```bash
pytest -q
bash scripts/security/check_secret_leaks.sh
```

If you changed auth/rate-limit behavior, ensure these pass:

- `eval/test_api_auth.py`
- `eval/test_api_rate_limit.py`

If you changed exam/evaluation behavior, ensure these pass:

- `eval/test_offline_eval.py`
- `eval/test_api_eval.py`

## Security and Secrets

- Never commit real credentials or tokens.
- Keep `.env` local only; use `.env.example` placeholders.
- Put runtime secrets in deployment environment (for example, GitHub Secrets / VM env file).
- Do not embed API keys in frontend code.

## Code Guidelines

- Keep changes focused and minimal.
- Preserve schema contracts in `src/models/schemas.py`.
- Prefer explicit, testable logic over prompt-only behavior.
- Maintain offline fallback behavior where applicable.
- Keep auth behavior consistent:
  - `/healthz` public
  - `/v1/*` protected only when `ENTRA_AUTH_ENABLED=true`

## Frontend Contributions

- Keep responsive behavior on desktop and mobile.
- Preserve clear session states (start, active, submitting, submitted).
- Avoid adding debug-only UI text to production flow.
- If adding screenshots for docs/demo, place files in `screenshots/`.

Recommended screenshot names:

- `session-setup.png`
- `exam-accordion.png`
- `evaluation-summary.png`
- `grounded-explanations.png`

## Pull Request Checklist

Before requesting review:

1. Tests pass locally (`pytest -q`).
2. Secret leak guard passes.
3. Docs updated when behavior changes (`README.md`, `docs/demo.md`, or this file).
4. No personal values in examples (`.env.example` stays generic).
5. PR description includes:
   - what changed
   - why it changed
   - how it was validated

## CI/CD Notes

- CI runs on PRs and pushes to `main` via `.github/workflows/deploy_vm.yml`.
- VM deployment is triggered on push/workflow dispatch and is optional for local development.

