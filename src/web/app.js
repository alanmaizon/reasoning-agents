const state = {
  config: null,
  msal: null,
  token: null,
  account: null,
  exam: null,
  answers: {},
};

const MSAL_SOURCES = [
  "/web/vendor/msal-browser.min.js",
  "https://cdn.jsdelivr.net/npm/@azure/msal-browser@5.2.0/lib/msal-browser.min.js",
  "https://unpkg.com/@azure/msal-browser@5.2.0/lib/msal-browser.min.js",
];

let msalLoadPromise = null;

const el = {
  authBanner: document.getElementById("authBanner"),
  authMeta: document.getElementById("authMeta"),
  loginBtn: document.getElementById("loginBtn"),
  logoutBtn: document.getElementById("logoutBtn"),
  startForm: document.getElementById("startForm"),
  userId: document.getElementById("userId"),
  focusTopics: document.getElementById("focusTopics"),
  minutes: document.getElementById("minutes"),
  offlineMode: document.getElementById("offlineMode"),
  planMeta: document.getElementById("planMeta"),
  examContainer: document.getElementById("examContainer"),
  submitBtn: document.getElementById("submitBtn"),
  misconceptionList: document.getElementById("misconceptionList"),
  lessonList: document.getElementById("lessonList"),
  groundedList: document.getElementById("groundedList"),
};

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
  el.authBanner.className = `banner ${kind}`;
  el.authBanner.textContent = message;
}

function setAuthMeta() {
  if (state.account) {
    const who =
      state.account.username ||
      state.account.name ||
      state.account.localAccountId ||
      "signed-in";
    el.authMeta.textContent = `Signed in: ${who}`;
    return;
  }
  el.authMeta.textContent = "Not signed in";
}

function setFormsEnabled(enabled) {
  el.startForm.querySelector("button[type=submit]").disabled = !enabled;
  if (!enabled) {
    el.submitBtn.disabled = true;
  }
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

function renderExam(exam) {
  if (!exam?.questions?.length) {
    el.examContainer.innerHTML = "";
    return;
  }

  el.examContainer.innerHTML = exam.questions
    .map((q) => {
      const choices = q.choices
        .map(
          (choice, idx) => `
            <label class="choice">
              <input type="radio" name="q_${escapeHtml(q.id)}" value="${idx}" />
              <span>${escapeHtml(choice)}</span>
            </label>
          `
        )
        .join("");

      return `
        <article class="question">
          <div class="q-head">Q${escapeHtml(q.id)} · ${escapeHtml(q.domain)}</div>
          <div>${escapeHtml(q.stem)}</div>
          <div>${choices}</div>
        </article>
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
  } catch {
    const tokenResult = await state.msal.acquireTokenPopup(request);
    state.token = tokenResult.accessToken;
  }
}

async function handleLogin() {
  if (!state.msal) {
    throw new Error("MSAL is not initialized.");
  }
  const loginResult = await state.msal.loginPopup({
    scopes: [state.config.api_scope],
    prompt: "select_account",
  });
  state.account = loginResult.account;
  state.token = null;
  await ensureToken();
  setAuthMeta();
  setBanner("info", "Authentication successful.");
  setFormsEnabled(true);
}

async function initAuth() {
  const cfg = state.config;
  if (!cfg.auth_enabled) {
    setBanner("info", "API auth disabled. Calls are made without bearer tokens.");
    el.loginBtn.style.display = "none";
    el.logoutBtn.style.display = "none";
    setFormsEnabled(true);
    return;
  }

  if (!cfg.client_id || !cfg.api_scope || !cfg.authority) {
    setBanner(
      "error",
      "Auth is enabled but frontend auth config is incomplete (client_id/api_scope/authority)."
    );
    setFormsEnabled(false);
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
    return;
  }

  const accounts = state.msal.getAllAccounts();
  if (accounts.length > 0) {
    state.account = accounts[0];
    try {
      await ensureToken();
      setBanner("info", "Authenticated session restored.");
      setFormsEnabled(true);
    } catch {
      setBanner("warn", "Session found but token refresh failed. Please sign in again.");
      setFormsEnabled(false);
    }
  } else {
    setBanner("warn", "Auth enabled. Sign in to use /v1 endpoints.");
    setFormsEnabled(false);
  }
  setAuthMeta();
}

async function startSession(event) {
  event.preventDefault();
  try {
    await ensureToken();
    const payload = {
      user_id: el.userId.value.trim(),
      focus_topics: el.focusTopics.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      minutes: Number(el.minutes.value || 25),
      offline: Boolean(el.offlineMode.checked),
    };
    const result = await apiJson("/v1/session/start", payload);

    state.exam = result.exam;
    state.answers = {};

    const domains = (result.plan?.domains || []).join(", ");
    el.planMeta.textContent = `Plan domains: ${domains || "n/a"} | Questions: ${
      result.exam?.questions?.length || 0
    }`;
    renderExam(result.exam);
    el.submitBtn.disabled = false;

    if ((result.warnings || []).length > 0) {
      setBanner("warn", `Session started with warnings: ${result.warnings.join(" | ")}`);
    } else {
      setBanner("info", "Session started.");
    }
  } catch (err) {
    setBanner("error", `Start failed: ${err.message}`);
  }
}

async function submitSession() {
  if (!state.exam) {
    return;
  }
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
    setBanner("info", "Submission completed.");
  } catch (err) {
    setBanner("error", `Submit failed: ${err.message}`);
  }
}

function bindEvents() {
  el.startForm.addEventListener("submit", startSession);
  el.examContainer.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "radio") {
      return;
    }
    const qid = target.name.replace(/^q_/, "");
    state.answers[qid] = Number(target.value);
  });
  el.submitBtn.addEventListener("click", submitSession);
  el.loginBtn.addEventListener("click", async () => {
    try {
      await handleLogin();
    } catch (err) {
      setBanner("error", `Login failed: ${err.message}`);
    }
  });
  el.logoutBtn.addEventListener("click", async () => {
    state.token = null;
    state.account = null;
    setFormsEnabled(!state.config.auth_enabled);
    setAuthMeta();
    if (state.msal?.logoutPopup) {
      try {
        await state.msal.logoutPopup();
      } catch {
        // Ignore local logout errors.
      }
    }
    setBanner("warn", "Signed out.");
  });
}

async function init() {
  bindEvents();
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
