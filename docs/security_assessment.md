# Security Assessment (AppSec + Cloud Security)

Date: 2026-02-26  
Repository: `alanmaizon/reasoning-agents`

## A. Executive Summary

- The repository already includes positive controls: MCP tool allow-listing, authentication support for `/v1/*`, and a CI secret-leak guard.
- The highest-risk gap is GitHub Actions token scope: workflow lacked explicit permissions, which can leave the default `GITHUB_TOKEN` over-privileged.
- CI actions are pinned to major versions (`@v4`, `@v5`) rather than immutable SHAs, leaving a supply-chain drift window.
- Deployment trust-on-first-use host key handling (`StrictHostKeyChecking=accept-new`) allows first-connection MITM risk on SSH deploy paths.
- Runtime dependency pinning is broad (`>=` in `requirements.txt`) and installs without hash verification in both CI and VM deploy script, increasing dependency substitution risk.
- The Docker image runs as root, which increases impact of runtime compromise.
- Prompt-injection risk to privileged tooling is partially mitigated: only read-only MCP tools are called from grounding logic and tool policy denies unknown tool names.
- This PR adds stronger tool-name normalization/validation and CI permission hardening with minimal code churn.
- Recommended next steps are to pin action SHAs, add stronger secret/dependency scanning, and tighten deployment host-key verification.

## B. Architecture & Trust Boundaries

### Text Diagram

```text
[User CLI/API Client] --(untrusted input: topics, answers, tokens)--> [FastAPI/Workflow]
   |                                                              |
   |                                                              +--> [State Store (local JSON / Postgres / Blob)]
   |                                                              |
   +--> [Agent Orchestration + Prompt Construction] --------------+--> [Foundry model runtime]
                                                                      |
                                                                      +--> [MCP Tools (Microsoft Learn search/fetch only)]

[GitHub Actions CI/CD] --(repo code + secrets)--> [VM over SSH] --> [Systemd service + runtime env]
```

### Dataflow Notes

1. Untrusted API/CLI inputs flow into planner/examiner/misconception prompts.
2. Grounding layer may call MCP tools via Foundry runtime.
3. CI runs tests and secret scan on PR/push, then deploys to VM on push/dispatch with secrets.
4. Deployment scripts package repo and remote-execute install/restart commands over SSH.

## C. Threat Model

### Assets

- GitHub secrets (VM SSH key, DB credentials, Azure keys)
- Production VM host and service environment (`/etc/mdt-api.env`)
- Model/tool execution boundary (MCP calls)
- User/study session data and auth tokens

### Entry Points

- FastAPI endpoints (`/v1/session/start`, `/v1/session/submit`)
- CLI text input
- GitHub workflow triggers (`pull_request`, `push`, `workflow_dispatch`)
- Dependency and action resolution during CI/deploy

### Attackers

- External user submitting crafted prompt/API payloads
- Malicious contributor in PR supply chain
- Network attacker on first SSH host key trust path
- Compromised upstream dependency/action publisher

### Assumptions

- Azure/VM runtime and GitHub Secrets are configured correctly
- No direct shell/tool execution from user prompts besides controlled MCP path
- Production secrets are not committed to git

## D. Findings Table

| Severity | Title | Evidence | Impact | Fix |
|---|---|---|---|---|
| High | Missing explicit workflow token permissions | `.github/workflows/deploy_vm.yml` had no `permissions` block | Default token scope may permit more repo operations than required if a step is compromised | Added top-level `permissions: contents: read` |
| Medium | Actions not pinned to immutable SHAs | `.github/workflows/deploy_vm.yml` uses `actions/checkout@v4`, `actions/setup-python@v5` | Tag retarget or compromised release stream can affect CI integrity | Pin actions to commit SHAs (see PR-ready diff section) |
| Medium | TOFU host key behavior in deploy pipeline | `.github/workflows/deploy_vm.yml` + `scripts/azure/deploy_vm_code.sh` use `StrictHostKeyChecking=accept-new` and runtime `ssh-keyscan` | First connection MITM can redirect deploy and capture secrets | Store/pin known host fingerprint in repo/secret and enforce strict verification |
| Medium | Unpinned/hashes-unverified Python dependencies | `requirements.txt` uses `>=`; deploy scripts run `pip install -r requirements.txt` | Supply-chain downgrade/substitution risk and non-reproducible builds | Move to locked constraints with `--require-hashes` for CI/deploy |
| Medium | Container runs as root | `Dockerfile` has no `USER` instruction | App compromise yields root privileges inside container | Add non-root user and ownership before runtime |
| Low | Lightweight secret scanner only | `scripts/security/check_secret_leaks.sh` regex-based scan | Misses many secret patterns and encoded formats | Add gitleaks/trufflehog + push-protection and keep current script as fast pre-check |
| Low | Prompt-to-tool boundary depended on raw tool names | `src/orchestration/tool_policy.py` previously only exact membership check | Ambiguous casing/whitespace input could create policy confusion and weak auditability | Added normalization + strict tool-name regex + tests |

## E. Prompt Injection Boundary Analysis

### Untrusted Input Sources

- API request bodies and auth headers (`src/api.py`)
- CLI terminal input (`src/orchestration/workflow.py`)
- Potentially untrusted model output re-ingested by parser (`src/util/jsonio.py`, agent flow)

### Reachable Tools/Actions

- MCP tool invocation from `run_grounding_verifier` via `foundry_run.run_mcp_tool`
- File writes for state/cache (`student_state.json`, `cache.json`)
- No direct shell/package-manager execution from user prompts in app runtime

### Current Mitigations

- Allow-list of MCP tools (`microsoft_docs_search`, `microsoft_docs_fetch`, `microsoft_code_sample_search`)
- Deny-by-default approval handler for unknown tools
- Grounding fallback when evidence unavailable / invalid
- Pydantic schema validation for request and model payloads

### Gaps

- Tool-name canonicalization was implicit and weak
- No separate policy engine for context-aware approvals (tool + args + caller)
- No taint-tagging from user input through prompt assembly

### Recommended Guardrails

1. Enforce canonical tool-name sanitizer + regex gate (implemented in this PR).
2. Add argument-level policy checks (allowed keys, max lengths, URL/domain constraints).
3. Require explicit approval path for any future non-read-only tools.
4. Log denied tool calls as security events and add alerting thresholds.
5. Add prompt-injection regression tests for malicious tool-name/argument payloads.

## F. Infrastructure Security Review

- **IAM least privilege:** Uses `DefaultAzureCredential`; recommend managed identity + minimal RBAC scopes for storage/state.
- **Secret storage/rotation:** `.env` ignored and CI uses GitHub Secrets; add explicit rotation cadence and incident rotation playbook in `docs/runbook_ops.md`.
- **Network segmentation:** VM SSH deploy implies public management path; recommend NSG allowlist and private endpoint where possible.
- **Runtime isolation:** Docker defaults to root; enforce non-root user and consider read-only root FS.
- **Logging/auditing:** App has structured logging; add SIEM forwarding and security-event dashboards for auth failures and tool denials.

## G. CI/CD Security Review

- **Permissions/token scope:** Hardened in this PR with `permissions: contents: read`.
- **Triggers:** Uses `pull_request`/`push`/`workflow_dispatch`; avoids risky `pull_request_target`.
- **Cache/artifacts:** No artifact publishing currently; low artifact poisoning exposure.
- **Release/provenance:** No signed release/provenance attestation configured; recommend artifact signing and provenance if release pipeline is added.
- **Dependency/action pinning:** Action and dependency pinning should be tightened.

## H. Remediation Plan

### Quick Wins (today)

- [x] Add explicit GitHub workflow permissions (done)
- [x] Harden tool policy normalization and deny malformed tool names (done)
- [ ] Pin GH Actions to immutable SHAs
- [ ] Add non-root Docker runtime user

### Medium-Term (1–2 weeks)

- [ ] Replace TOFU SSH host validation with pinned host keys/fingerprints
- [ ] Introduce locked dependency file + hash verification in CI/deploy
- [ ] Add stronger secret scanning (gitleaks) in CI and pre-commit

### Longer-Term (1–2 months)

- [ ] Add policy-as-code gate for agent tool calls (tool + args + caller context)
- [ ] Add security telemetry/alerting playbooks (auth failures, tool denial spikes, deploy anomalies)
- [ ] Adopt provenance/signed build outputs for release artifacts

## I. PR-Ready Change Set Suggestions

### Files modified in this PR

- `.github/workflows/deploy_vm.yml`
- `src/orchestration/tool_policy.py`
- `eval/test_tool_policy.py`
- `docs/security_assessment.md` (this report)

### Additional proposed changes (not yet applied)

1. **Workflow SHA pinning**
   - File: `.github/workflows/deploy_vm.yml`
   - Replace version tags with commit SHAs for `actions/checkout` and `actions/setup-python`.

2. **Policy code: argument allowlist**
   - File: `src/orchestration/tool_policy.py`
   - Add per-tool argument schema allowlist and value length limits.

3. **Prompt-injection boundary tests**
   - File: `eval/test_tool_policy.py` (or new `eval/test_prompt_injection_boundaries.py`)
   - Add tests for malicious argument keys, overlong values, and non-`learn.microsoft.com` URLs.

4. **Secret scanning + pre-commit**
   - Files to add: `.pre-commit-config.yaml`, update `CONTRIBUTING.md`
   - Include gitleaks hook and keep existing shell script for quick local checks.
