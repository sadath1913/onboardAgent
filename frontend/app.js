const state = {
  repositoryId: localStorage.getItem("onboard.repositoryId") || "",
  repository: null,
  view: "overview",
  conversationId: null,
  selectedFile: "",
  pollTimer: null,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return body;
}

function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.add("show");
  setTimeout(() => box.classList.remove("show"), 2600);
}

function showRepositoryForm(show = true) {
  $("#repoFormPanel").classList.toggle("hidden", !show);
  $("#workspace").classList.toggle("hidden", show || !state.repository);
  $("#emptyState").classList.toggle("hidden", show || Boolean(state.repository));
  if (show) $("#repoUrl").focus();
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (state.repository) renderView();
}

async function refreshRepositories() {
  const result = await api("/api/repositories");
  const select = $("#repoSelect");
  const repositories = result.repositories || [];

  select.innerHTML = repositories.length
    ? repositories.map((repo) => (
      `<option value="${escapeHtml(repo.id)}">${escapeHtml(repo.owner)}/${escapeHtml(repo.name)}</option>`
    )).join("")
    : '<option value="">No repository yet</option>';

  if (state.repositoryId && repositories.some((repo) => repo.id === state.repositoryId)) {
    select.value = state.repositoryId;
  } else if (repositories.length) {
    state.repositoryId = repositories[0].id;
    select.value = state.repositoryId;
    localStorage.setItem("onboard.repositoryId", state.repositoryId);
  } else {
    state.repositoryId = "";
  }

  if (state.repositoryId) {
    await loadRepository();
  } else {
    state.repository = null;
    showRepositoryForm(false);
    $("#emptyState").classList.remove("hidden");
  }
}

async function loadRepository() {
  if (!state.repositoryId) return;

  try {
    state.repository = await api(`/api/repositories/${encodeURIComponent(state.repositoryId)}`);
    $("#crumbRepo").textContent = `${state.repository.owner}/${state.repository.name}`;
    $("#repoOwner").textContent = state.repository.owner.toUpperCase();
    $("#repoTitle").textContent = state.repository.name;
    $("#repoLink").href = `https://github.com/${encodeURIComponent(state.repository.owner)}/${encodeURIComponent(state.repository.name)}`;
    $("#repoBranch").textContent = state.repository.default_branch
      ? `branch | ${state.repository.default_branch}`
      : "branch | default";

    const active = ["queued", "processing"].includes(state.repository.status);
    $("#jobBanner").classList.toggle("hidden", !active);
    $("#jobTitle").textContent = state.repository.status === "queued"
      ? "Analysis queued"
      : "Mapping your repository";
    $("#jobDetail").textContent = state.repository.status === "queued"
      ? "The background worker will start shortly."
      : "Scanning files, symbols, APIs, generating embeddings and onboarding guide…";
    $("#errorBanner").classList.toggle(
      "hidden",
      !(state.repository.status === "failed" && state.repository.error),
    );
    $("#errorBanner").textContent = state.repository.error || "";
    $("#workspace").classList.remove("hidden");
    $("#emptyState").classList.add("hidden");
    $("#repoFormPanel").classList.add("hidden");

    if (active) {
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(loadRepository, 1800);
    } else {
      clearTimeout(state.pollTimer);
    }
    if (state.repository.status === "completed") await renderView();
  } catch (error) {
    toast(error.message);
  }
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value || 0);
}

function statCard(title, value, caption) {
  return `
    <div class="stat-card">
      <div class="stat-title">${escapeHtml(title)}</div>
      <div class="stat-number">${formatNumber(value)}</div>
      <div class="stat-caption">${escapeHtml(caption)}</div>
    </div>`;
}

function pageHeading(title, subtitle, withSearch = false) {
  return `
    <div class="page-header">
      <div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(subtitle)}</p></div>
      ${withSearch ? '<input id="viewSearch" class="search-input" placeholder="Search names and paths..." />' : ""}
    </div>`;
}

async function renderView() {
  if (!state.repository || state.repository.status !== "completed") {
    $("#viewContent").innerHTML = "";
    return;
  }

  const root = $("#viewContent");
  if (state.view === "overview") return renderOverview(root);
  if (state.view === "guide") return renderGuide(root);
  if (state.view === "files") return renderFiles(root);
  if (state.view === "apis") return renderApis(root);
  if (state.view === "chat") return renderChat(root);
}

async function renderOverview(root) {
  const [data, guide] = await Promise.all([
    api(`/api/repositories/${state.repositoryId}/overview`),
    api(`/api/repositories/${state.repositoryId}/guide`),
  ]);
  const extensions = [...new Set((data.languages || []).map((item) => item.extension).filter(Boolean))];
  const directories = (guide.structure || []).slice(0, 9).map((item) => `
    <li>
      <span class="directory-path">${escapeHtml(item.path === "." ? "Root files" : `${item.path}/`)}</span>
      <span class="directory-meta">${item.file_count} files</span>
    </li>`).join("");
  const symbols = (data.important_symbols || []).slice(0, 8).map((item) => `
    <li class="symbol-row">
      <div>
        <span class="directory-path">${escapeHtml(item.qualified_name)}</span>
        <div class="symbol-meta">${escapeHtml(item.file_path)}:${item.start_line}</div>
      </div>
      <span class="tag neutral">${escapeHtml(item.kind)}</span>
    </li>`).join("");

  root.innerHTML = `
    <div class="stats-grid">
      ${statCard("FILES MAPPED", data.counts.files, "Text files within analysis limits")}
      ${statCard("CODE SYMBOLS", data.counts.symbols, "Functions, classes and types")}
      ${statCard("CHUNKS / EMBEDDINGS", (data.counts.chunks || 0) + " / " + (data.counts.embeddings || 0), "Semantic chunks indexed for RAG")}
      ${statCard("API ROUTES", data.counts.api_routes, "Route patterns found in source")}
    </div>
    <div class="overview-grid">
      <div>
        <section class="panel">
          <div class="panel-heading"><h3>Project introduction</h3><button data-action="guide">Open guide -&gt;</button></div>
          <div class="readme">${escapeHtml(data.readme_excerpt || "No README was found in the scanned files.")}</div>
        </section>
        <section class="panel">
          <div class="panel-heading"><h3>Languages detected</h3><span class="directory-meta">BY SOURCE FILES</span></div>
          <div class="tag-row">${extensions.length
            ? extensions.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join("")
            : '<span class="tag neutral">Could not confirm</span>'}</div>
          <p class="empty-copy">${data.llm_enabled
            ? "IBM / Groq LLM is active — answers are grounded in repository evidence."
            : "Chat uses hybrid RAG search. Configure IBM_API_KEY or GROQ_API_KEY for LLM answers."}</p>
        </section>
      </div>
      <section class="panel">
        <div class="panel-heading"><h3>Repository structure</h3><button data-action="files">Browse files -&gt;</button></div>
        <ul class="directory-list">${directories || '<li class="empty-copy">No scanned files yet.</li>'}</ul>
      </section>
    </div>
    <section class="panel">
      <div class="panel-heading"><h3>Notable code symbols</h3><button data-action="files">Explore symbols -&gt;</button></div>
      <ul class="symbol-list">${symbols || '<li class="empty-copy">No symbols were extracted from the scanned languages.</li>'}</ul>
    </section>`;

  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.action));
  });
}

function listItems(items, emptyText, renderItem) {
  return items.length ? items.map(renderItem).join("") : `<li>${escapeHtml(emptyText)}</li>`;
}

async function renderGuide(root) {
  const guide = await api(`/api/repositories/${state.repositoryId}/guide`);

  // If we have LLM-generated Markdown, render it
  const hasLLMGuide = guide.content_md && guide.content_md.length > 100;

  if (hasLLMGuide) {
    const downloadBar = `
      <div class="guide-download-bar">
        <span class="guide-generated-note">Generated ${guide.generated_at ? new Date(guide.generated_at).toLocaleString() : "recently"}</span>
        <div class="guide-download-buttons">
          <a class="secondary-button" href="/api/repositories/${encodeURIComponent(state.repositoryId)}/guide/download?format=markdown" download="onboarding-guide.md">↓ Markdown</a>
          <a class="secondary-button" href="/api/repositories/${encodeURIComponent(state.repositoryId)}/guide/download?format=pdf" download="onboarding-guide.pdf">↓ PDF</a>
        </div>
      </div>`;
    root.innerHTML = `${pageHeading("Onboarding guide", "AI-generated developer onboarding guide grounded in repository evidence.")}
      ${downloadBar}
      <div class="guide-layout">
        <div class="guide-content guide-content-full">
          <section class="content-card guide-section">
            <div class="guide-md-body">${markdownLite(guide.content_md)}</div>
          </section>
        </div>
      </div>`;
    return;
  }

  // Fallback: structured static guide sections
  const sections = [
    {
      id: "overview",
      title: "Project overview",
      body: `
        <p><strong>${escapeHtml((guide.overview || {}).repository || "")}</strong></p>
        <p>${escapeHtml((guide.overview || {}).description || "Project purpose could not be confirmed from a README.")}</p>
        <p>Stack: ${escapeHtml(((guide.overview || {}).technology_stack || []).join(", "))}</p>
        <p>Detected: ${((guide.overview || {}).counts || {}).files || 0} files | ${((guide.overview || {}).counts || {}).symbols || 0} symbols | ${((guide.overview || {}).counts || {}).api_routes || 0} API routes</p>`,
    },
    {
      id: "structure",
      title: "Repository structure",
      body: `<ul>${listItems(guide.structure || [], "No structure could be confirmed.", (item) => `
        <li><code>${escapeHtml(item.path === "." ? "Root files" : `${item.path}/`)}</code> - ${item.file_count} scanned files; examples: ${escapeHtml(item.examples.join(", "))}</li>` )}</ul>`,
    },
    {
      id: "architecture",
      title: "Architecture and relationships",
      body: `
        <p>${(guide.architecture || {}).resolved_import_edges || 0} local import relationships were resolved from source evidence.</p>
        <pre>${escapeHtml((guide.architecture || {}).import_graph_mermaid || "No import graph available.")}</pre>`,
    },
    {
      id: "entry-points",
      title: "Application entry points",
      body: `<ul>${listItems(guide.entry_points || [], "Could not confirm an entry point by common filename.", (path) => `<li><code>${escapeHtml(path)}</code></li>`)}</ul>`,
    },
    {
      id: "apis",
      title: "API routes",
      body: `<ul>${listItems((guide.api_routes || []).slice(0, 60), "No supported route patterns were found.", (item) => `
        <li><code>${escapeHtml(item.method)} ${escapeHtml(item.path)}</code> - <code>${escapeHtml(item.file_path)}:${item.line_number}</code> (${escapeHtml(item.handler)})</li>` )}</ul>`,
    },
    {
      id: "data",
      title: "Dependencies and configuration",
      body: `
        <p>Manifest files: ${escapeHtml((guide.manifest_files || []).join(", ") || "none found")}</p>
        <p>External import names: ${escapeHtml((guide.external_imports || []).join(", ") || "none found")}</p>
        <p>Environment variable names (values are redacted): ${escapeHtml((guide.environment_variables || []).join(", ") || "could not confirm")}</p>`,
    },
    {
      id: "workflow",
      title: "Local setup and tests",
      body: `
        <ul>${listItems(guide.setup_and_test_commands || [], "Could not confirm exact commands from manifests.", (command) => `<li><code>${escapeHtml(command)}</code></li>`)}</ul>
        <p>Test files: ${escapeHtml((guide.test_files || []).slice(0, 35).join(", ") || "none detected")}</p>`,
    },
    {
      id: "deployment",
      title: "Deployment",
      body: `<p>Detected deployment or CI files: ${escapeHtml((guide.deployment_files || []).join(", ") || "could not confirm")}</p>`,
    },
    {
      id: "limits",
      title: "Analysis limits",
      body: `<ul>${listItems(guide.limitations || [], "No limitations were recorded.", (item) => `<li>${escapeHtml(item)}</li>`)}</ul>`,
    },
  ];

  const downloadBar = `
    <div class="guide-download-bar">
      <div class="guide-download-buttons">
        <a class="secondary-button" href="/api/repositories/${encodeURIComponent(state.repositoryId)}/guide/download?format=markdown" download="onboarding-guide.md">↓ Markdown</a>
        <a class="secondary-button" href="/api/repositories/${encodeURIComponent(state.repositoryId)}/guide/download?format=pdf" download="onboarding-guide.pdf">↓ PDF</a>
      </div>
    </div>`;

  const tableOfContents = sections.map(({ id, title }) => `
    <button data-jump="${id}">${escapeHtml(title)}</button>`).join("");
  const sectionContent = sections.map(({ id, title, body }) => `
    <section class="content-card guide-section" id="guide-${id}">
      <h3>${escapeHtml(title)}</h3>${body}
    </section>`).join("");

  root.innerHTML = `${pageHeading(
    "Onboarding guide",
    "A repository-backed introduction, limited to what the scanner could confirm.",
  )}
    ${downloadBar}
    <div class="guide-layout">
      <nav class="content-card guide-toc">${tableOfContents}</nav>
      <div class="guide-content">${sectionContent}</div>
    </div>`;

  root.querySelectorAll("[data-jump]").forEach((button) => {
    button.addEventListener("click", () => {
      $(`#guide-${button.dataset.jump}`).scrollIntoView({ behavior: "smooth" });
    });
  });
}

async function renderFiles(root) {
  const [files, symbols] = await Promise.all([
    api(`/api/repositories/${state.repositoryId}/files?limit=500`),
    api(`/api/repositories/${state.repositoryId}/symbols?limit=500`),
  ]);
  const fileRows = files.files.map((item) => `
    <li>
      <button class="file-row" data-path="${escapeHtml(item.path)}">
        <div>
          <span class="file-path">${escapeHtml(item.path)}</span>
          <span class="file-kind">${escapeHtml(item.kind)} | ${item.line_count} lines</span>
        </div>
        <span class="directory-meta">${Math.ceil(item.size / 1024)} KB</span>
      </button>
    </li>`).join("");

  root.innerHTML = `${pageHeading(
    "Files and symbols",
    `${files.files.length} scanned files | ${symbols.symbols.length} symbols in the current page`,
    true,
  )}
    <div class="file-grid">
      <section class="content-card file-browser">
        <ul id="fileList" class="file-list">${fileRows || '<li class="empty-copy">No matching files.</li>'}</ul>
      </section>
      <section class="content-card">
        <div id="fileDetail" class="view-empty">Choose a file to inspect its sanitized source and extracted symbols.</div>
      </section>
    </div>`;

  async function openFile(path) {
    state.selectedFile = path;
    const detail = await api(
      `/api/repositories/${state.repositoryId}/files/content?path=${encodeURIComponent(path)}`,
    );
    root.querySelectorAll(".file-row").forEach((button) => {
      button.classList.toggle("selected", button.dataset.path === path);
    });

    const symbolPills = detail.symbols.map((item) => `
      <span class="symbol-pill">${escapeHtml(item.kind)} ${escapeHtml(item.qualified_name)} | L${item.start_line}</span>`).join("");
    $("#fileDetail").outerHTML = `
      <div id="fileDetail">
        <div class="panel-heading">
          <div>
            <div class="directory-path">${escapeHtml(detail.path)}</div>
            <div class="directory-meta">${detail.line_count} lines | ${escapeHtml(detail.kind)}</div>
          </div>
          <button class="text-button" id="copyPath">Copy path</button>
        </div>
        <div class="symbol-pills">${symbolPills || '<span class="empty-copy">No symbols extracted.</span>'}</div>
        <pre class="code-view">${escapeHtml(detail.content)}</pre>
      </div>`;
    $("#copyPath").addEventListener("click", async () => {
      await navigator.clipboard.writeText(detail.path);
      toast("Path copied");
    });
  }

  root.querySelectorAll(".file-row").forEach((button) => {
    button.addEventListener("click", () => openFile(button.dataset.path));
  });
  $("#viewSearch").addEventListener("input", () => {
    const value = $("#viewSearch").value.toLowerCase();
    root.querySelectorAll(".file-row").forEach((button) => {
      button.closest("li").classList.toggle("hidden", !button.dataset.path.toLowerCase().includes(value));
    });
  });
  if (state.selectedFile) await openFile(state.selectedFile).catch(() => { state.selectedFile = ""; });
}

async function renderApis(root) {
  const data = await api(`/api/repositories/${state.repositoryId}/apis`);
  const routes = data.routes.map((item) => `
    <li class="route-row">
      <span class="route-method ${item.method.toLowerCase()}">${escapeHtml(item.method)}</span>
      <span class="route-path">${escapeHtml(item.path)}</span>
      <button class="route-file" data-file="${escapeHtml(item.file_path)}">${escapeHtml(item.file_path)}:${item.line_number}</button>
      <span class="directory-meta">${escapeHtml(item.handler)}</span>
    </li>`).join("");

  root.innerHTML = `${pageHeading(
    "API routes",
    `${data.routes.length} route patterns found. These are source matches, not runtime-verified endpoints.`,
    true,
  )}
    <section class="content-card">
      <ul id="routeList" class="route-list">${routes || '<li class="view-empty">No API route pattern was found by the supported parsers.</li>'}</ul>
    </section>`;

  root.querySelectorAll(".route-file").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedFile = button.dataset.file;
      setView("files");
    });
  });
  $("#viewSearch").addEventListener("input", () => {
    const value = $("#viewSearch").value.toLowerCase();
    root.querySelectorAll(".route-row").forEach((row) => {
      row.classList.toggle("hidden", !row.textContent.toLowerCase().includes(value));
    });
  });
}

function markdownLite(value) {
  const lines = escapeHtml(value).split("\n");
  let html = "";
  let inCode = false;
  let code = [];

  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        html += `<pre>${code.join("\n")}</pre>`;
        code = [];
        inCode = false;
      } else {
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }

    if (line.startsWith("### ")) {
      html += `<h3>${line.slice(4)}</h3>`;
      continue;
    }
    if (line.startsWith("## ")) {
      html += `<h2>${line.slice(3)}</h2>`;
      continue;
    }
    if (line.startsWith("# ")) {
      html += `<h1>${line.slice(2)}</h1>`;
      continue;
    }
    if (line.startsWith("- ") || line.startsWith("* ")) {
      html += `<li>${line.slice(2).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>")}</li>`;
      continue;
    }
    if (!line.trim()) {
      html += "<br/>";
      continue;
    }

    const formatted = line
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    html += `<p>${formatted}</p>`;
  }
  if (code.length) html += `<pre>${code.join("\n")}</pre>`;
  return html;
}

function renderMessage(role, content, evidence = []) {
  const messageContent = role === "assistant" ? markdownLite(content) : escapeHtml(content);
  const citations = evidence.map((item) => `
    <button class="evidence-chip" data-source="${escapeHtml(item.path)}">
      ${escapeHtml(item.path)}:${item.start_line}-${item.end_line}
    </button>`).join("");
  return `
    <div class="message ${role}">
      ${messageContent}
      ${citations ? `<div class="evidence-list">${citations}</div>` : ""}
    </div>`;
}

function addEvidenceClickHandlers(root, messages) {
  messages.querySelectorAll("[data-source]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedFile = button.dataset.source;
      setView("files");
    });
  });
}

async function renderChat(root) {
  root.innerHTML = `${pageHeading(
    "Ask the codebase",
    "Ask how a feature works, where logic lives, or which files connect.",
  )}
    <div class="chat-shell">
      <section class="content-card chat-main">
        <div id="chatMessages" class="chat-messages">
          <div class="chat-welcome">
            <div class="welcome-icon">*</div>
            <h3>Start with a codebase question.</h3>
            <p>Answers search repository evidence first. When configured, an LLM helps explain the retrieved code, and source references stay attached.</p>
            <div class="suggestions">
              <button class="suggestion">Where are the entry points?</button>
              <button class="suggestion">Show authentication code</button>
              <button class="suggestion">How do API routes connect?</button>
            </div>
          </div>
        </div>
        <form id="chatForm">
          <div class="chat-input-row">
            <textarea id="chatInput" placeholder="Ask a question about this repository..." maxlength="4000" required></textarea>
            <button class="primary-button" type="submit">Ask <span>-&gt;</span></button>
          </div>
          <div class="chat-hint">ENTER to send | SHIFT + ENTER for a new line</div>
        </form>
      </section>
      <aside class="content-card chat-side">
        <h3>Grounded in this repository</h3>
        <p>Retrieved evidence is shown with every answer. Chat follows repository relationships when related files are useful.</p>
        <div class="tag-row"><span class="tag">Source citations</span><span class="tag">Follow-up context</span></div>
        <h3 class="chat-recent-heading">Recent conversations</h3>
        <div id="chatHistory" class="empty-copy">Conversations appear here as you ask questions.</div>
      </aside>
    </div>`;

  const messages = $("#chatMessages");
  const historyList = $("#chatHistory");
  const newConversation = document.createElement("button");
  newConversation.className = "history-item";
  newConversation.textContent = "+ New conversation";
  newConversation.addEventListener("click", () => {
    state.conversationId = null;
    renderChat(root);
  });
  historyList.before(newConversation);

  if (state.conversationId) {
    const history = await api(
      `/api/repositories/${state.repositoryId}/conversations/${state.conversationId}`,
    ).catch(() => null);
    if (history) {
      messages.innerHTML = history.messages
        .map((item) => renderMessage(item.role, item.content, item.evidence || []))
        .join("");
      addEvidenceClickHandlers(root, messages);
    }
  }

  root.querySelectorAll(".suggestion").forEach((button) => {
    button.addEventListener("click", () => {
      $("#chatInput").value = button.textContent;
      $("#chatInput").focus();
    });
  });
  $("#chatInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chatForm").requestSubmit();
    }
  });
  $("#chatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = $("#chatInput").value.trim();
    if (!question) return;

    const welcome = messages.querySelector(".chat-welcome");
    if (welcome) welcome.remove();
    messages.insertAdjacentHTML("beforeend", renderMessage("user", question));
    $("#chatInput").value = "";
    const submit = event.submitter;
    if (submit) submit.disabled = true;

    try {
      const answer = await api(`/api/repositories/${state.repositoryId}/chat`, {
        method: "POST",
        body: JSON.stringify({ question, conversation_id: state.conversationId }),
      });
      state.conversationId = answer.conversation_id;
      messages.insertAdjacentHTML(
        "beforeend",
        renderMessage("assistant", answer.answer, answer.evidence),
      );
      addEvidenceClickHandlers(root, messages);
      messages.scrollTop = messages.scrollHeight;
      await loadConversationList();
    } catch (error) {
      messages.insertAdjacentHTML(
        "beforeend",
        renderMessage("assistant", `I could not complete that request: ${error.message}`),
      );
    } finally {
      if (submit) submit.disabled = false;
    }
  });
  await loadConversationList();
}

async function loadConversationList() {
  const list = $("#chatHistory");
  if (!list) return;
  const result = await api(`/api/repositories/${state.repositoryId}/conversations`);
  if (!result.conversations.length) {
    list.textContent = "Conversations appear here as you ask questions.";
    return;
  }

  list.innerHTML = result.conversations.map((item) => `
    <button class="history-item" data-conversation="${escapeHtml(item.id)}">
      ${escapeHtml((item.preview || "Conversation").slice(0, 75))}
    </button>`).join("");
  list.querySelectorAll("[data-conversation]").forEach((button) => {
    button.addEventListener("click", () => {
      state.conversationId = button.dataset.conversation;
      renderChat($("#viewContent"));
    });
  });
}

$("#repoForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = $("#repoUrl").value.trim();
  const message = $("#formMessage");
  const button = event.submitter;
  message.textContent = "Checking the GitHub URL...";
  button.disabled = true;

  try {
    await api("/api/repositories/validate", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    const result = await api("/api/repositories", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    state.repositoryId = result.repository_id;
    state.conversationId = null;
    localStorage.setItem("onboard.repositoryId", state.repositoryId);
    await refreshRepositories();
    toast(result.status === "failed" ? result.error : "Repository analysis queued");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#repoSelect").addEventListener("change", () => {
  state.repositoryId = $("#repoSelect").value;
  state.conversationId = null;
  state.selectedFile = "";
  localStorage.setItem("onboard.repositoryId", state.repositoryId);
  loadRepository();
});

$("#showAddRepo").addEventListener("click", () => showRepositoryForm(true));
$("#heroAddRepo").addEventListener("click", () => showRepositoryForm(true));
$("#closeRepoForm").addEventListener("click", () => showRepositoryForm(false));

$("#reanalyzeButton").addEventListener("click", async () => {
  try {
    const result = await api(`/api/repositories/${state.repositoryId}/analyze`, {
      method: "POST",
      body: "{}",
    });
    toast(result.status === "failed" ? result.error : "Analysis queued");
    await loadRepository();
  } catch (error) {
    toast(error.message);
  }
});

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

api("/api/health")
  .then((data) => {
    const llm = data.llm_enabled ? " · LLM active" : "";
    const db = data.db_healthy === false ? " · DB offline" : "";
    $("#serviceStatus").textContent = `Local analysis service ready${llm}${db}`;
  })
  .catch(() => { $("#serviceStatus").textContent = "Local service unavailable"; });
refreshRepositories().catch((error) => {
  $("#serviceStatus").textContent = "Local service unavailable";
  showRepositoryForm(true);
  toast(error.message);
});
