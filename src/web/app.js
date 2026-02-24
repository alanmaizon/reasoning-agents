const state = {
  config: null,
  msal: null,
  token: null,
  account: null,
  exam: null,
  sessionMode: "adaptive",
  answers: {},
  planMetaBase: "",
  authBusy: false,
  submitting: false,
  examLocked: false,
  examSubmitted: false,
};

const MSAL_SOURCES = [
  "/web/vendor/msal-browser.min.js",
  "https://cdn.jsdelivr.net/npm/@azure/msal-browser@5.2.0/lib/msal-browser.min.js",
  "https://unpkg.com/@azure/msal-browser@5.2.0/lib/msal-browser.min.js",
];
const GOOGLE_USERNAME_RETRY_KEY = "condor_google_username_retry";
const INTERACTIVE_LOGIN_PROMPT = "login";

let msalLoadPromise = null;

const el = {
  authBanner: document.getElementById("authBanner"),
  authMeta: document.getElementById("authMeta"),
  loginBtn: document.getElementById("loginBtn"),
  logoutBtn: document.getElementById("logoutBtn"),
  identityDetails: document.getElementById("identityDetails"),
  identityValue: document.getElementById("identityValue"),
  startForm: document.getElementById("startForm"),
  userId: document.getElementById("userId"),
  userIdRow: document.getElementById("userIdRow"),
  sessionMode: document.getElementById("sessionMode"),
  focusTopicsBlock: document.getElementById("focusTopicsBlock"),
  focusTopicsCustom: document.getElementById("focusTopicsCustom"),
  offlineMode: document.getElementById("offlineMode"),
  planExamPanel: document.getElementById("planExamPanel"),
  examLockHint: document.getElementById("examLockHint"),
  planMeta: document.getElementById("planMeta"),
  examContainer: document.getElementById("examContainer"),
  submitBtn: document.getElementById("submitBtn"),
  coachingPanel: document.getElementById("coachingPanel"),
  resultsPlaceholder: document.getElementById("resultsPlaceholder"),
  evaluationSection: document.getElementById("evaluationSection"),
  answerReviewSection: document.getElementById("answerReviewSection"),
  coachingGrid: document.getElementById("coachingGrid"),
  groundedSection: document.getElementById("groundedSection"),
  evaluationSummary: document.getElementById("evaluationSummary"),
  answerReviewList: document.getElementById("answerReviewList"),
  misconceptionList: document.getElementById("misconceptionList"),
  lessonList: document.getElementById("lessonList"),
  groundedList: document.getElementById("groundedList"),
};

const AZ900_PASS_SCORE = 700;
const AZ900_SCORE_MAX = 1000;
const AZ900_EXAM_TIME_MINUTES = 45;

function modeLabel(mode) {
  if (mode === "mock_test") {
    return "Mock AZ-900 test";
  }
  return "Adaptive coaching";
}

function syncModeInputs() {
  const isMockTest = (el.sessionMode.value || "adaptive") === "mock_test";
  el.focusTopicsBlock.disabled = isMockTest;
  el.focusTopicsCustom.disabled = isMockTest;
  el.focusTopicsCustom.placeholder = isMockTest
    ? "Not used in mock test mode"
    : "Optional custom topics, comma-separated";
  if (isMockTest) {
    el.focusTopicsCustom.value = "";
    el.focusTopicsBlock
      .querySelectorAll('input[type="checkbox"]')
      .forEach((checkbox) => {
        checkbox.checked = false;
      });
  }
}

function selectedFocusTopics() {
  const topics = new Set();
  if (el.focusTopicsBlock && !el.focusTopicsBlock.disabled) {
    el.focusTopicsBlock
      .querySelectorAll('input[type="checkbox"]:checked')
      .forEach((checkbox) => {
        const value = checkbox.value.trim();
        if (value) {
          topics.add(value);
        }
      });
  }

  const custom = (el.focusTopicsCustom?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  for (const value of custom) {
    topics.add(value);
  }
  return Array.from(topics);
}

function hasMsal() {
  return Boolean(window.msal?.PublicClientApplication);
}

async function createMsalClient(config) {
  const client = new window.msal.PublicClientApplication(config);
  if (typeof client.initialize === "function") {
    await client.initialize();
  }
  return client;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.crossOrigin = "anonymous";
    script.referrerPolicy = "no-referrer";
    script.onload = () => resolve(src);
    script.onerror = () => {
      script.remove();
      reject(new Error(`Failed to load script: ${src}`));
    };
    document.head.appendChild(script);
  });
}

async function ensureMsalLoaded() {
  if (hasMsal()) {
    return;
  }
  if (!msalLoadPromise) {
    msalLoadPromise = (async () => {
      let lastError = null;
      for (const src of MSAL_SOURCES) {
        try {
          await loadScript(src);
          if (hasMsal()) {
            return;
          }
          lastError = new Error(`MSAL namespace missing after loading: ${src}`);
        } catch (err) {
          lastError = err;
        }
      }
      throw lastError || new Error("Unable to load MSAL library.");
    })();
  }

  try {
    await msalLoadPromise;
  } finally {
    if (!hasMsal()) {
      msalLoadPromise = null;
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setBanner(kind, message) {
  el.authBanner.classList.remove("is-hidden");
  el.authBanner.className = `banner ${kind}`;
  el.authBanner.textContent = message;
}

function hideBanner() {
  el.authBanner.classList.add("is-hidden");
  el.authBanner.textContent = "";
}

function syncAuthActions() {
  if (!state.config?.auth_enabled) {
    el.loginBtn.style.display = "none";
    el.logoutBtn.style.display = "none";
    return;
  }
  if (state.account) {
    el.loginBtn.style.display = "none";
    el.logoutBtn.style.display = "";
    return;
  }
  el.loginBtn.style.display = "";
  el.logoutBtn.style.display = "none";
}

function setAuthMeta() {
  if (!state.config?.auth_enabled) {
    el.authMeta.textContent = "";
    el.authMeta.classList.add("is-hidden");
    if (el.identityDetails && el.identityValue) {
      el.identityDetails.classList.add("is-hidden");
      el.identityValue.textContent = "";
    }
    return;
  }

  if (state.account) {
    const principal =
      state.account.username ||
      state.account.name ||
      state.account.localAccountId ||
      "signed-in";
    el.authMeta.textContent = "";
    el.authMeta.classList.add("is-hidden");
    if (el.identityDetails && el.identityValue) {
      el.identityDetails.classList.remove("is-hidden");
      el.identityValue.textContent = principal;
    }
    return;
  }
  el.authMeta.classList.remove("is-hidden");
  el.authMeta.textContent = "Sign in is required to start a session.";
  if (el.identityDetails && el.identityValue) {
    el.identityDetails.classList.add("is-hidden");
    el.identityValue.textContent = "";
  }
}

function deriveUserIdFromAccount() {
  if (!state.account) {
    return "signed-in-user";
  }
  return (
    state.account.localAccountId ||
    state.account.homeAccountId ||
    state.account.username ||
    state.account.name ||
    "signed-in-user"
  );
}

function syncUserIdInput() {
  const authEnabled = Boolean(state.config?.auth_enabled);
  if (!authEnabled) {
    el.userIdRow.classList.remove("is-hidden");
    el.userId.disabled = false;
    el.userId.removeAttribute("aria-readonly");
    el.userId.removeAttribute("title");
    return;
  }

  el.userIdRow.classList.add("is-hidden");
  el.userId.disabled = true;
  el.userId.setAttribute("aria-readonly", "true");
  el.userId.title = "Derived from your sign-in identity.";
  el.userId.value = deriveUserIdFromAccount();
}

function setFormsEnabled(enabled) {
  el.startForm.querySelector("button[type=submit]").disabled = !enabled;
  if (!enabled) {
    el.submitBtn.disabled = true;
  }
}

function setPlanExamVisible(visible) {
  el.planExamPanel.classList.toggle("is-hidden", !visible);
  el.planExamPanel.setAttribute("aria-hidden", visible ? "false" : "true");
}

function setCoachingPanelVisible(visible) {
  el.coachingPanel.classList.toggle("is-hidden", !visible);
  el.coachingPanel.setAttribute("aria-hidden", visible ? "false" : "true");
}

function setResultsVisibility(visible) {
  el.resultsPlaceholder.classList.toggle("is-hidden", visible);
  el.evaluationSection.classList.toggle("is-hidden", !visible);
  el.answerReviewSection.classList.toggle("is-hidden", !visible);
  el.coachingGrid.classList.toggle("is-hidden", !visible);
  el.groundedSection.classList.toggle("is-hidden", !visible);
}

function setExamAccessibility(locked, message = "") {
  state.examLocked = Boolean(locked);
  const panel = el.planExamPanel;
  if (panel) {
    panel.classList.toggle("locked", state.examLocked);
    if (state.examLocked) {
      panel.setAttribute("aria-disabled", "true");
    } else {
      panel.removeAttribute("aria-disabled");
    }
  }

  if (el.examLockHint) {
    if (state.examLocked && message) {
      el.examLockHint.textContent = message;
      el.examLockHint.hidden = false;
    } else {
      el.examLockHint.hidden = true;
      el.examLockHint.textContent = "";
    }
  }

  el.examContainer.querySelectorAll("input, select").forEach((control) => {
    control.disabled = state.examLocked || state.submitting || state.examSubmitted;
  });

  const hasExam = Boolean(state.exam?.questions?.length);
  el.submitBtn.disabled = !hasExam || state.examLocked || state.submitting || state.examSubmitted;
}

function msalErrorCode(err) {
  if (!err || typeof err !== "object") {
    return "";
  }
  return String(err.errorCode || err.code || "").toLowerCase();
}

function isMsalError(err, ...codes) {
  const code = msalErrorCode(err);
  return codes.some((c) => code === String(c).toLowerCase());
}

function isGoogleUsernameParamError(err) {
  const text = String(err?.message || "").toLowerCase();
  return text.includes("invalid_request") && text.includes("username");
}

function interactiveAuthRequest(scope) {
  const request = {
    scopes: [scope],
    prompt: INTERACTIVE_LOGIN_PROMPT,
  };
  const idpHint = String(state.config?.idp_hint || "").trim();
  const domainHint = String(state.config?.domain_hint || "").trim();
  const extraQueryParameters = {};
  if (idpHint) {
    extraQueryParameters.idp = idpHint;
  }
  if (domainHint) {
    extraQueryParameters.domain_hint = domainHint;
  }
  if (Object.keys(extraQueryParameters).length > 0) {
    request.extraQueryParameters = extraQueryParameters;
  }
  return request;
}

async function resetCachedAuthState() {
  state.token = null;
  state.account = null;
  if (!state.msal || typeof state.msal.clearCache !== "function") {
    return;
  }

  try {
    const accounts = state.msal.getAllAccounts();
    if (accounts.length === 0) {
      await state.msal.clearCache();
      return;
    }
    for (const account of accounts) {
      await state.msal.clearCache({ account });
    }
  } catch {
    // Ignore cache-reset errors and continue with interactive login fallback.
  }
}

function setAuthBusy(busy) {
  state.authBusy = busy;
  el.loginBtn.disabled = busy;
  el.logoutBtn.disabled = busy;
}

async function apiJson(path, payload) {
  const headers = { "Content-Type": "application/json" };
  if (state.config.auth_enabled) {
    if (!state.token) {
      throw new Error("Sign in first to call API endpoints.");
    }
    headers.Authorization = `Bearer ${state.token}`;
  }

  const response = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  const raw = await response.text();
  let data = null;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail = data?.detail || `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }

  return data;
}

function stripChoicePrefix(choice) {
  const text = String(choice);
  const match = text.match(/^[A-D]\)\s*(.*)$/);
  return match ? match[1] : text;
}

function isDropdownQuestion(question) {
  return typeof question?.stem === "string" && question.stem.includes("[Dropdown Menu]");
}

function lowercaseInlineContinuation(text) {
  const clean = String(text || "");
  if (clean.length < 2) {
    return clean;
  }
  const first = clean[0];
  const second = clean[1];
  if (first >= "A" && first <= "Z" && second >= "a" && second <= "z") {
    return `${first.toLowerCase()}${clean.slice(1)}`;
  }
  return clean;
}

function renderDropdownSentence(question) {
  const select = `<select class="dropdown-answer" data-qid="${escapeHtml(question.id)}"><option value="">Select an option</option>${question.choices
    .map((choice, idx) => `<option value="${idx}">${escapeHtml(stripChoicePrefix(choice))}</option>`)
    .join("")}</select>`;

  const token = "[Dropdown Menu]";
  const stem = String(question.stem || "").replace(/\s+/g, " ").trim();
  if (!stem.includes(token)) {
    return `<p class="dropdown-sentence"><span>${escapeHtml(stem)}</span> ${select}</p>`;
  }

  const parts = stem.split(token);
  const before = String(parts.shift() || "").replace(/\s+/g, " ").trim();
  let after = String(parts.join(token) || "").replace(/\s+/g, " ").trim();
  const beVerbMatch = after.match(/^(is|are)\s+(.+)$/i);
  if (beVerbMatch) {
    const verb = beVerbMatch[1].toLowerCase();
    const rest = lowercaseInlineContinuation(beVerbMatch[2]);
    after = `${verb} ${rest}`;
  }
  if (/\b(is|are)\s*$/i.test(before)) {
    after = lowercaseInlineContinuation(after);
  }
  return `<p class="dropdown-sentence"><span>${escapeHtml(before)}</span> ${select} <span>${escapeHtml(after)}</span></p>`;
}

function updateQuestionProgress() {
  if (!state.exam?.questions?.length) {
    return;
  }

  let answered = 0;
  for (const question of state.exam.questions) {
    const hasAnswer = typeof state.answers[question.id] === "number";
    if (hasAnswer) {
      answered += 1;
    }

    const statusEl = el.examContainer.querySelector(`[data-qstatus="${question.id}"]`);
    if (!statusEl) {
      continue;
    }
    statusEl.textContent = hasAnswer ? "Answered" : "Not answered";
    statusEl.classList.toggle("answered", hasAnswer);
    statusEl.classList.toggle("unanswered", !hasAnswer);
  }

  if (state.planMetaBase) {
    el.planMeta.textContent = `${state.planMetaBase} | Answered: ${answered}/${state.exam.questions.length}`;
  }
}

function renderExam(exam) {
  if (!exam?.questions?.length) {
    el.examContainer.innerHTML = "";
    return;
  }

  el.examContainer.innerHTML = exam.questions
    .map((q, idx) => {
      const answerControl = isDropdownQuestion(q)
        ? renderDropdownSentence(q)
        : q.choices
            .map(
              (choice, choiceIdx) => `
                <label class="choice">
                  <input type="radio" name="q_${escapeHtml(q.id)}" value="${choiceIdx}" />
                  <span>${escapeHtml(stripChoicePrefix(choice))}</span>
                </label>
              `
            )
            .join("");

      return `
        <details class="question accordion-question" data-question-id="${escapeHtml(q.id)}" ${idx === 0 ? "open" : ""}>
          <summary class="accordion-summary">
            <span class="q-head">Q${escapeHtml(q.id)} · ${escapeHtml(q.domain)}</span>
            <span class="question-status unanswered" data-qstatus="${escapeHtml(q.id)}">Not answered</span>
          </summary>
          <div class="accordion-body">
            ${isDropdownQuestion(q) ? "" : `<p>${escapeHtml(q.stem)}</p>`}
            <div>${answerControl}</div>
          </div>
        </details>
      `;
    })
    .join("");
}

function renderResults(result) {
  const misconceptions = result?.diagnosis?.top_misconceptions || [];
  const lessons = result?.coaching?.lesson_points || [];
  const grounded = result?.grounded || [];

  el.misconceptionList.innerHTML = misconceptions.length
    ? misconceptions.map((m) => `<li>${escapeHtml(m)}</li>`).join("")
    : "<li>No misconceptions detected.</li>";

  el.lessonList.innerHTML = lessons.length
    ? lessons.map((p) => `<li>${escapeHtml(p)}</li>`).join("")
    : "<li>No lesson points returned.</li>";

  el.groundedList.innerHTML = grounded.length
    ? grounded
        .map((g) => {
          const cites = (g.citations || [])
            .map(
              (c) =>
                `<li><a href="${escapeHtml(c.url)}" target="_blank" rel="noreferrer">${escapeHtml(c.title)}</a>: ${escapeHtml(c.snippet)}</li>`
            )
            .join("");
          return `
            <article class="grounded-item">
              <div class="mono small">Question ${escapeHtml(g.question_id)}</div>
              <p>${escapeHtml(g.explanation)}</p>
              <ul class="result-list">${cites}</ul>
            </article>
          `;
        })
        .join("")
    : "<p class='small'>No grounded explanations (all answers may be correct).</p>";
}

function renderEvaluationSummary(diagnosis) {
  if (!state.exam?.questions?.length) {
    el.evaluationSummary.innerHTML = "<p class='small'>No quiz loaded.</p>";
    return;
  }

  const questions = state.exam.questions;
  const total = questions.length;
  const diagnosisById = new Map((diagnosis?.results || []).map((r) => [r.id, r]));
  const correctCount = questions.reduce((count, question) => {
    const byDiagnosis = diagnosisById.get(question.id);
    if (typeof byDiagnosis?.correct === "boolean") {
      return count + (byDiagnosis.correct ? 1 : 0);
    }
    return count + (state.answers[question.id] === question.answer_key ? 1 : 0);
  }, 0);
  const unansweredCount = questions.reduce(
    (count, question) => count + (typeof state.answers[question.id] === "number" ? 0 : 1),
    0
  );

  const accuracy = total > 0 ? correctCount / total : 0;
  const accuracyPct = Math.round(accuracy * 100);
  const scaledScore = Math.round(accuracy * AZ900_SCORE_MAX);
  const passed = scaledScore >= AZ900_PASS_SCORE;
  const resultText = passed ? "Pass estimate" : "Below pass estimate";
  const modeNote =
    state.sessionMode === "mock_test"
      ? "Mock mode uses a randomized 40-60 question range. This scaled score is an estimate."
      : "Adaptive mode is coaching-focused. This scaled score is an estimate.";

  el.evaluationSummary.innerHTML = `
    <div class="eval-grid">
      <div class="eval-chip">
        <div class="eval-label">Estimated score</div>
        <div class="eval-value">${scaledScore}/${AZ900_SCORE_MAX}</div>
      </div>
      <div class="eval-chip">
        <div class="eval-label">Correct answers</div>
        <div class="eval-value">${correctCount}/${total}</div>
      </div>
      <div class="eval-chip">
        <div class="eval-label">Accuracy</div>
        <div class="eval-value">${accuracyPct}%</div>
      </div>
      <div class="eval-chip">
        <div class="eval-label">Estimated result</div>
        <div class="eval-value eval-status ${passed ? "pass" : "warn"}">${resultText}</div>
      </div>
    </div>
    <p class="small">
      AZ-900 pass target: ${AZ900_PASS_SCORE}/${AZ900_SCORE_MAX}. Fundamentals exam time is about ${AZ900_EXAM_TIME_MINUTES} minutes and production exams are typically 40-60 questions.
    </p>
    <p class="small">${modeNote}${unansweredCount > 0 ? ` Unanswered: ${unansweredCount}.` : ""}</p>
  `;
}

function clearResults() {
  el.evaluationSummary.innerHTML = "<p class='small'>Submit your quiz to calculate your estimated AZ-900 score.</p>";
  el.answerReviewList.innerHTML = "<p class='small'>Submit your quiz to see answer review.</p>";
  el.misconceptionList.innerHTML = "<li>Start a session and submit answers.</li>";
  el.lessonList.innerHTML = "<li>Start a session and submit answers.</li>";
  el.groundedList.innerHTML = "<p class='small'>No grounded explanations yet.</p>";
}

function renderAnswerReview(diagnosis) {
  if (!state.exam?.questions?.length) {
    el.answerReviewList.innerHTML = "<p class='small'>No quiz loaded.</p>";
    return;
  }

  const diagById = new Map((diagnosis?.results || []).map((r) => [r.id, r]));
  el.answerReviewList.innerHTML = state.exam.questions
    .map((q) => {
      const selected = state.answers[q.id];
      const diag = diagById.get(q.id);
      const correct = diag ? Boolean(diag.correct) : selected === q.answer_key;
      const selectedText =
        typeof selected === "number" && q.choices[selected]
          ? q.choices[selected]
          : "No answer submitted";
      const correctText = q.choices[q.answer_key] || "n/a";
      const why =
        typeof diag?.why === "string" && diag.why.trim()
          ? diag.why.trim()
          : q.rationale_draft;

      return `
        <article class="review-item ${correct ? "review-correct" : "review-wrong"}">
          <div class="review-head">
            <span class="mono small">Q${escapeHtml(q.id)} · ${escapeHtml(q.domain)}</span>
            <span class="review-badge">${correct ? "Correct" : "Incorrect"}</span>
          </div>
          <p>${escapeHtml(q.stem)}</p>
          <p><strong>Your answer:</strong> ${escapeHtml(selectedText)}</p>
          <p><strong>Correct answer:</strong> ${escapeHtml(correctText)}</p>
          <p class="small"><strong>Why:</strong> ${escapeHtml(why)}</p>
        </article>
      `;
    })
    .join("");
}

async function ensureToken() {
  if (!state.config.auth_enabled) {
    return;
  }
  if (!state.account) {
    throw new Error("Sign in first.");
  }
  if (state.token) {
    return;
  }

  const request = { scopes: [state.config.api_scope], account: state.account };
  try {
    const tokenResult = await state.msal.acquireTokenSilent(request);
    state.token = tokenResult.accessToken;
  } catch (err) {
    if (isGoogleUsernameParamError(err)) {
      await resetCachedAuthState();
      await state.msal.loginRedirect(interactiveAuthRequest(state.config.api_scope));
      throw new Error("Google sign-in needs a fresh session. Redirecting...");
    }
    if (isMsalError(err, "interaction_in_progress")) {
      throw new Error(
        "Authentication already in progress. Finish the open sign-in tab and retry."
      );
    }
    if (
      isMsalError(
        err,
        "interaction_required",
        "login_required",
        "consent_required",
        "no_tokens_found",
        "token_refresh_required"
      )
    ) {
      await state.msal.acquireTokenRedirect(
        interactiveAuthRequest(state.config.api_scope)
      );
      throw new Error("Redirecting to refresh authentication...");
    }
    throw err;
  }
}

async function handleLogin() {
  if (!state.msal) {
    throw new Error("MSAL is not initialized.");
  }
  if (state.authBusy) {
    throw new Error("Authentication already in progress.");
  }

  setAuthBusy(true);
  const googleHint =
    String(state.config?.idp_hint || "").toLowerCase().includes("google") ||
    String(state.config?.domain_hint || "").toLowerCase() === "google";
  setBanner("info", googleHint ? "Redirecting to Google sign-in..." : "Redirecting to sign-in...");
  setFormsEnabled(false);
  sessionStorage.removeItem(GOOGLE_USERNAME_RETRY_KEY);
  try {
    await state.msal.loginRedirect(interactiveAuthRequest(state.config.api_scope));
  } catch (err) {
    if (isMsalError(err, "interaction_in_progress")) {
      throw new Error(
        "Sign-in already in progress. Complete the existing login tab."
      );
    }
    throw err;
  } finally {
    setAuthBusy(false);
  }
}

async function initAuth() {
  const cfg = state.config;
  const googleHint =
    String(cfg?.idp_hint || "").toLowerCase().includes("google") ||
    String(cfg?.domain_hint || "").toLowerCase() === "google";
  el.loginBtn.textContent = googleHint ? "Sign In with Google" : "Sign In";

  if (!cfg.auth_enabled) {
    setBanner("info", "API auth disabled. Calls are made without bearer tokens.");
    syncAuthActions();
    setFormsEnabled(true);
    syncUserIdInput();
    setAuthMeta();
    return;
  }

  if (!cfg.client_id || !cfg.api_scope || !cfg.authority) {
    setBanner(
      "error",
      "Auth is enabled but frontend auth config is incomplete (client_id/api_scope/authority)."
    );
    setFormsEnabled(false);
    syncAuthActions();
    syncUserIdInput();
    return;
  }

  setBanner("info", "Loading authentication library...");
  try {
    await ensureMsalLoaded();
  } catch (err) {
    setBanner(
      "error",
      `MSAL failed to load from available sources. ${err.message}`
    );
    setFormsEnabled(false);
    syncAuthActions();
    syncUserIdInput();
    return;
  }

  try {
    state.msal = await createMsalClient({
      auth: {
        clientId: cfg.client_id,
        authority: cfg.authority,
        redirectUri: window.location.origin + "/",
      },
      cache: { cacheLocation: "sessionStorage" },
    });
  } catch (err) {
    setBanner("error", `MSAL initialization failed. ${err.message}`);
    setFormsEnabled(false);
    syncAuthActions();
    syncUserIdInput();
    return;
  }

  let redirectResult = null;
  try {
    redirectResult = await state.msal.handleRedirectPromise();
  } catch (err) {
    if (isGoogleUsernameParamError(err)) {
      const retried = sessionStorage.getItem(GOOGLE_USERNAME_RETRY_KEY) === "1";
      if (!retried) {
        sessionStorage.setItem(GOOGLE_USERNAME_RETRY_KEY, "1");
        await resetCachedAuthState();
        setBanner(
          "warn",
          "Google sign-in returned an invalid request. Retrying..."
        );
        await state.msal.loginRedirect(interactiveAuthRequest(cfg.api_scope));
        return;
      }
      sessionStorage.removeItem(GOOGLE_USERNAME_RETRY_KEY);
    }
    if (isMsalError(err, "interaction_in_progress")) {
      setBanner(
        "warn",
        "Authentication is still in progress. Finish sign-in in the opened tab and refresh."
      );
    } else {
      setBanner("error", `Failed to process auth redirect: ${err.message}`);
    }
    setFormsEnabled(false);
    setAuthMeta();
    syncAuthActions();
    syncUserIdInput();
    return;
  }

  if (redirectResult?.account) {
    state.account = redirectResult.account;
    state.token = redirectResult.accessToken || null;
    sessionStorage.removeItem(GOOGLE_USERNAME_RETRY_KEY);
  }

  const accounts = state.msal.getAllAccounts();
  if (!state.account && accounts.length > 0) {
    state.account = accounts[0];
  }

  if (state.account) {
    if (redirectResult?.account) {
      setBanner("info", "Authentication successful.");
    } else {
      setBanner("info", "Authenticated session restored.");
    }
    setFormsEnabled(true);
  } else {
    hideBanner();
    setFormsEnabled(false);
  }
  setAuthMeta();
  syncAuthActions();
  syncUserIdInput();
}

async function startSession(event) {
  event.preventDefault();
  try {
    await ensureToken();
    const payload = {
      user_id: el.userId.value.trim(),
      mode: el.sessionMode.value || "adaptive",
      focus_topics: selectedFocusTopics(),
      offline: Boolean(el.offlineMode.checked),
    };
    const result = await apiJson("/v1/session/start", payload);

    state.exam = result.exam;
    state.sessionMode = result.mode || payload.mode;
    state.answers = {};
    state.submitting = false;
    state.examSubmitted = false;

    const domains = (result.plan?.domains || []).join(", ");
    const modeText = modeLabel(state.sessionMode);
    state.planMetaBase = `Mode: ${modeText} | Plan domains: ${domains || "n/a"} | Questions: ${result.exam?.questions?.length || 0}`;
    el.planMeta.textContent = state.planMetaBase;
    setPlanExamVisible(true);
    setCoachingPanelVisible(false);
    renderExam(result.exam);
    clearResults();
    setResultsVisibility(false);
    updateQuestionProgress();
    setExamAccessibility(false);

    if ((result.warnings || []).length > 0) {
      setBanner("warn", `Session started with warnings: ${result.warnings.join(" | ")}`);
    } else {
      setBanner("info", `${modeText} started.`);
    }
  } catch (err) {
    setBanner("error", `Start failed: ${err.message}`);
  }
}

async function submitSession() {
  if (!state.exam || state.submitting || state.examSubmitted) {
    return;
  }
  state.submitting = true;
  setExamAccessibility(true, "Submitting answers. Plan + Exam is locked.");
  try {
    await ensureToken();
    const payload = {
      user_id: el.userId.value.trim(),
      exam: state.exam,
      answers: { answers: state.answers },
      offline: Boolean(el.offlineMode.checked),
    };
    const result = await apiJson("/v1/session/submit", payload);
    renderResults(result);
    renderEvaluationSummary(result?.diagnosis);
    renderAnswerReview(result?.diagnosis);
    setCoachingPanelVisible(true);
    setResultsVisibility(true);
    state.examSubmitted = true;
    state.submitting = false;
    setExamAccessibility(true, "Answers submitted. Start a new session to edit or retake.");
    setPlanExamVisible(false);
    setBanner("info", "Submission completed.");
  } catch (err) {
    state.submitting = false;
    setExamAccessibility(false);
    setBanner("error", `Submit failed: ${err.message}`);
  }
}

function bindEvents() {
  el.startForm.addEventListener("submit", startSession);
  el.sessionMode.addEventListener("change", () => {
    syncModeInputs();
  });
  el.examContainer.addEventListener("toggle", (event) => {
    if (state.examLocked || state.submitting || state.examSubmitted) {
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLDetailsElement)) {
      return;
    }
    if (!target.classList.contains("accordion-question") || !target.open) {
      return;
    }
    el.examContainer
      .querySelectorAll("details.accordion-question[open]")
      .forEach((detailsEl) => {
        if (detailsEl !== target) {
          detailsEl.open = false;
        }
      });
  }, true);
  el.examContainer.addEventListener("change", (event) => {
    if (state.examLocked || state.submitting || state.examSubmitted) {
      return;
    }
    const target = event.target;
    if (target instanceof HTMLInputElement && target.type === "radio") {
      const qid = target.name.replace(/^q_/, "");
      state.answers[qid] = Number(target.value);
      updateQuestionProgress();
      return;
    }
    if (target instanceof HTMLSelectElement && target.classList.contains("dropdown-answer")) {
      const qid = target.dataset.qid;
      if (!qid) {
        return;
      }
      if (target.value === "") {
        delete state.answers[qid];
      } else {
        state.answers[qid] = Number(target.value);
      }
      updateQuestionProgress();
      return;
    }
  });
  el.submitBtn.addEventListener("click", submitSession);
  el.loginBtn.addEventListener("click", async () => {
    try {
      await handleLogin();
    } catch (err) {
      if (err.message?.startsWith("Redirecting")) {
        setBanner("info", err.message);
        return;
      }
      setBanner("error", `Login failed: ${err.message}`);
    }
  });
  el.logoutBtn.addEventListener("click", async () => {
    state.token = null;
    state.account = null;
    setAuthBusy(true);
    setFormsEnabled(!state.config.auth_enabled);
    setAuthMeta();
    syncAuthActions();
    syncUserIdInput();
    if (state.msal?.logoutRedirect) {
      try {
        await state.msal.logoutRedirect({
          postLogoutRedirectUri: window.location.origin + "/",
        });
      } catch {
        // Ignore local logout errors.
      }
    }
    setAuthBusy(false);
    setBanner("warn", "Signed out.");
  });
}

async function init() {
  bindEvents();
  syncModeInputs();
  clearResults();
  setCoachingPanelVisible(false);
  setResultsVisibility(false);
  setPlanExamVisible(false);
  setExamAccessibility(false);
  syncUserIdInput();
  try {
    const response = await fetch("/frontend-config");
    state.config = await response.json();
    await initAuth();
  } catch (err) {
    setBanner("error", `Frontend setup failed: ${err.message}`);
    setFormsEnabled(false);
  }
}

init();
