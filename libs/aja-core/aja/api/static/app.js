/**
 * AJA Mission Control — Telegram Mini App & PWA Frontend Core
 */

(function () {
  "use strict";

  // ── 1. Telegram WebApp SDK Initialization ─────────────────────────────────
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    document.body.classList.add("telegram-theme");
  }

  function triggerHaptic(type = "light") {
    if (tg && tg.HapticFeedback) {
      if (type === "success" || type === "error" || type === "warning") {
        tg.HapticFeedback.notificationOccurred(type);
      } else {
        tg.HapticFeedback.impactOccurred(type);
      }
    }
  }

  // ── 2. State & Elements ───────────────────────────────────────────────────
  let autoscroll = true;
  let ws = null;
  let wsReconnectTimer = null;
  let authToken = new URLSearchParams(window.location.search).get("token") || "";

  const elements = {
    tabs: document.querySelectorAll(".tab-pane"),
    navItems: document.querySelectorAll(".nav-item"),
    connectionStatus: document.getElementById("connection-status"),
    statusText: document.getElementById("status-text"),
    
    // Kanban
    listTodo: document.getElementById("list-todo"),
    listDoing: document.getElementById("list-doing"),
    listDone: document.getElementById("list-done"),
    listFailed: document.getElementById("list-failed"),
    countTodo: document.getElementById("count-todo"),
    countDoing: document.getElementById("count-doing"),
    countDone: document.getElementById("count-done"),
    countFailed: document.getElementById("count-failed"),
    btnOpenAddTask: document.getElementById("btn-open-add-task"),
    modalAddTask: document.getElementById("modal-add-task"),
    formAddTask: document.getElementById("form-add-task"),
    btnCancelTask: document.getElementById("btn-cancel-task"),
    inputTaskTitle: document.getElementById("input-task-title"),
    inputTaskDesc: document.getElementById("input-task-desc"),

    // Terminal
    terminalOutput: document.getElementById("terminal-output"),
    terminalForm: document.getElementById("terminal-form"),
    terminalCmdInput: document.getElementById("terminal-cmd-input"),
    btnClearTerm: document.getElementById("btn-clear-term"),
    btnAutoscroll: document.getElementById("btn-autoscroll"),

    // Approvals
    approvalsContainer: document.getElementById("approvals-container"),
    approvalsCountHeader: document.getElementById("approvals-count-header"),
    navApprovalsBadge: document.getElementById("nav-approvals-badge"),
    diffOutput: document.getElementById("diff-output"),

    // Telemetry
    statOs: document.getElementById("stat-os"),
    statPython: document.getElementById("stat-python"),
    statCpu: document.getElementById("stat-cpu"),
    statRam: document.getElementById("stat-ram"),
    statFragments: document.getElementById("stat-fragments"),
    workersList: document.getElementById("workers-list"),
  };

  // ── 3. Navigation ─────────────────────────────────────────────────────────
  elements.navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      triggerHaptic("light");
      const targetTabId = btn.dataset.tab;
      
      elements.navItems.forEach((b) => b.classList.remove("active"));
      elements.tabs.forEach((t) => t.classList.remove("active"));
      
      btn.classList.add("active");
      const targetPane = document.getElementById(targetTabId);
      if (targetPane) targetPane.classList.add("active");

      // Tab specific refresh
      if (targetTabId === "tab-kanban") loadTasks();
      if (targetTabId === "tab-approvals") { loadApprovals(); loadDiff(); }
      if (targetTabId === "tab-telemetry") loadTelemetry();
    });
  });

  // ── 4. API Helpers ────────────────────────────────────────────────────────
  function getHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (authToken) {
      headers["Authorization"] = `Bearer ${authToken}`;
      headers["X-AJA-Auth-Token"] = authToken;
    }
    return headers;
  }

  async function apiFetch(url, options = {}) {
    options.headers = { ...getHeaders(), ...(options.headers || {}) };
    try {
      const res = await fetch(url, options);
      if (res.status === 401 || res.status === 403) {
        console.warn("API Auth required for", url);
      }
      return res;
    } catch (err) {
      console.error("Fetch error for", url, err);
      throw err;
    }
  }

  // ── 5. Kanban Tasks Handling ──────────────────────────────────────────────
  async function loadTasks() {
    try {
      const res = await apiFetch("/memory/tasks");
      if (!res.ok) return;
      const data = await res.json();
      const tasks = Array.isArray(data) ? data : data.tasks || [];
      renderKanban(tasks);
    } catch (e) {
      console.error("loadTasks error:", e);
    }
  }

  function renderKanban(tasks) {
    const buckets = { todo: [], doing: [], done: [], failed: [] };

    tasks.forEach((task) => {
      const st = (task.status || "todo").toLowerCase();
      if (st.includes("progress") || st === "doing" || st === "running") {
        buckets.doing.push(task);
      } else if (st.includes("done") || st.includes("complete") || st === "finished") {
        buckets.done.push(task);
      } else if (st.includes("fail") || st.includes("error")) {
        buckets.failed.push(task);
      } else {
        buckets.todo.push(task);
      }
    });

    // Update Counts
    elements.countTodo.textContent = buckets.todo.length;
    elements.countDoing.textContent = buckets.doing.length;
    elements.countDone.textContent = buckets.done.length;
    elements.countFailed.textContent = buckets.failed.length;

    // Render columns
    renderColumn(elements.listTodo, buckets.todo, "No backlog tasks", "todo");
    renderColumn(elements.listDoing, buckets.doing, "No active tasks", "doing");
    renderColumn(elements.listDone, buckets.done, "No completed tasks", "done");
    renderColumn(elements.listFailed, buckets.failed, "No failed tasks", "failed");
  }

  function renderColumn(container, taskList, emptyText, colType) {
    if (!taskList.length) {
      container.innerHTML = `<div class="empty-placeholder">${emptyText}</div>`;
      return;
    }

    container.innerHTML = taskList
      .map((t) => {
        const id = t.id || t.task_id || "task";
        const title = t.title || t.objective || t.goal || "Task";
        const updated = t.updated_at ? new Date(t.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "";
        
        let actionButtons = "";
        if (colType === "todo") {
          actionButtons = `<button class="btn-mini" onclick="window.ajaMutateTask('${id}', 'in_progress')">▶ Start</button>`;
        } else if (colType === "doing") {
          actionButtons = `
            <button class="btn-mini" onclick="window.ajaCompleteTask('${id}')">✔ Complete</button>
            <button class="btn-mini" onclick="window.ajaMutateTask('${id}', 'failed')">✘ Fail</button>
          `;
        } else if (colType === "done" || colType === "failed") {
          actionButtons = `<button class="btn-mini" onclick="window.ajaArchiveTask('${id}')">Archive</button>`;
        }

        return `
          <div class="task-card" data-id="${id}">
            <div class="task-title">${escapeHtml(title)}</div>
            <div class="task-meta">
              <span>#${id.slice(0, 6)}</span>
              <span>${updated}</span>
            </div>
            ${actionButtons ? `<div class="task-actions">${actionButtons}</div>` : ""}
          </div>
        `;
      })
      .join("");
  }

  // Global window helpers for onclick handlers
  window.ajaMutateTask = async function (id, status) {
    triggerHaptic("medium");
    await apiFetch(`/memory/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: status }),
    });
    loadTasks();
  };

  window.ajaCompleteTask = async function (id) {
    triggerHaptic("success");
    await apiFetch(`/memory/tasks/${id}/complete`, { method: "POST" });
    loadTasks();
  };

  window.ajaArchiveTask = async function (id) {
    triggerHaptic("light");
    await apiFetch(`/memory/tasks/${id}/archive`, { method: "POST" });
    loadTasks();
  };

  // Add Task Modal Handlers
  elements.btnOpenAddTask.addEventListener("click", () => {
    triggerHaptic("light");
    elements.modalAddTask.classList.add("open");
    elements.inputTaskTitle.focus();
  });

  elements.btnCancelTask.addEventListener("click", () => {
    elements.modalAddTask.classList.remove("open");
  });

  elements.formAddTask.addEventListener("submit", async (e) => {
    e.preventDefault();
    triggerHaptic("success");
    const title = elements.inputTaskTitle.value.trim();
    const desc = elements.inputTaskDesc.value.trim();
    if (!title) return;

    try {
      await apiFetch("/memory/tasks", {
        method: "POST",
        body: JSON.stringify({ objective: title, instructions: desc, status: "todo" }),
      });
      elements.inputTaskTitle.value = "";
      elements.inputTaskDesc.value = "";
      elements.modalAddTask.classList.remove("open");
      loadTasks();
    } catch (err) {
      alert("Failed creating task: " + err);
    }
  });

  // ── 6. Approvals & Git Diff ───────────────────────────────────────────────
  async function loadApprovals() {
    try {
      const res = await apiFetch("/runtime/approvals");
      if (!res.ok) return;
      const data = await res.json();
      const approvals = Array.isArray(data) ? data : data.approvals || [];
      renderApprovals(approvals);
    } catch (e) {
      console.error("loadApprovals error:", e);
    }
  }

  function renderApprovals(approvals) {
    const pending = approvals.filter((a) => a.status === "pending" || !a.status);
    elements.approvalsCountHeader.textContent = pending.length;
    
    if (pending.length > 0) {
      elements.navApprovalsBadge.style.display = "block";
      elements.navApprovalsBadge.textContent = pending.length;
    } else {
      elements.navApprovalsBadge.style.display = "none";
    }

    if (!pending.length) {
      elements.approvalsContainer.innerHTML = `
        <div class="empty-placeholder" style="background: var(--bg-card); border-radius: var(--radius-md); padding: 32px">
          No pending high-risk command approvals. All systems clear.
        </div>
      `;
      return;
    }

    elements.approvalsContainer.innerHTML = pending
      .map((a) => {
        const id = a.id || a.approval_id || "appr";
        const cmd = a.command || a.action || "Command";
        const reasons = a.reasons ? a.reasons.join(", ") : a.reason || "High-risk command execution";
        const risk = a.risk_level || "HIGH";

        return `
          <div class="approval-card" data-id="${id}">
            <div class="approval-header">
              <span class="approval-badge">${escapeHtml(risk)} RISK</span>
              <span style="font-size: 11px; color: var(--text-muted)">ID: ${id.slice(0, 8)}</span>
            </div>
            <div class="approval-cmd">$ ${escapeHtml(cmd)}</div>
            <div class="approval-reasons">${escapeHtml(reasons)}</div>
            <div class="approval-btn-group">
              <button class="btn-approve" onclick="window.ajaResolveApproval('${id}', true)">✔ Approve</button>
              <button class="btn-deny" onclick="window.ajaResolveApproval('${id}', false)">✘ Deny</button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  window.ajaResolveApproval = async function (id, approved) {
    triggerHaptic(approved ? "success" : "error");
    const endpoint = approved ? "/runtime/approve" : "/runtime/deny";
    try {
      await apiFetch(endpoint, {
        method: "POST",
        body: JSON.stringify({ id: id }),
      });
      loadApprovals();
    } catch (e) {
      console.error("resolve approval failed:", e);
    }
  };

  async function loadDiff() {
    try {
      const res = await apiFetch("/diff");
      if (!res.ok) {
        elements.diffOutput.textContent = "Clean working tree (No uncommitted changes).";
        return;
      }
      const data = await res.json();
      const diffText = typeof data === "string" ? data : data.diff || "";
      if (!diffText.trim()) {
        elements.diffOutput.textContent = "Clean working tree (No uncommitted changes).";
        return;
      }
      renderColorizedDiff(diffText);
    } catch (e) {
      elements.diffOutput.textContent = "Clean working tree.";
    }
  }

  function renderColorizedDiff(diff) {
    const lines = diff.split("\n");
    elements.diffOutput.innerHTML = lines
      .map((line) => {
        if (line.startsWith("+")) return `<span class="diff-add">${escapeHtml(line)}</span>`;
        if (line.startsWith("-")) return `<span class="diff-del">${escapeHtml(line)}</span>`;
        if (line.startsWith("@")) return `<span class="diff-info">${escapeHtml(line)}</span>`;
        return escapeHtml(line);
      })
      .join("\n");
  }

  // ── 7. Telemetry Stats ────────────────────────────────────────────────────
  async function loadTelemetry() {
    try {
      const res = await apiFetch("/status");
      if (!res.ok) return;
      const data = await res.json();

      elements.statOs.textContent = data.os || "Host Native";
      elements.statPython.textContent = `Python ${data.python || "3.11"}`;
      elements.statCpu.textContent = `${data.cores || 8} Cores`;
      elements.statRam.textContent = `RAM: ${data.memory_mb ? Math.round(data.memory_mb) + "MB" : "Optimal"}`;
      elements.statFragments.textContent = data.lancedb_fragments || "Compact";
    } catch (e) {
      console.error("loadTelemetry error:", e);
    }
  }

  // ── 8. Live Terminal & WebSocket Streaming ────────────────────────────────
  function appendTerminalLine(text, color = null) {
    const line = document.createElement("div");
    line.className = "terminal-line";
    if (color) line.style.color = color;
    line.textContent = text;
    elements.terminalOutput.appendChild(line);

    // Keep at most 1000 lines
    while (elements.terminalOutput.childElementCount > 1000) {
      elements.terminalOutput.removeChild(elements.terminalOutput.firstChild);
    }

    if (autoscroll) {
      elements.terminalOutput.scrollTop = elements.terminalOutput.scrollHeight;
    }
  }

  elements.btnClearTerm.addEventListener("click", () => {
    triggerHaptic("light");
    elements.terminalOutput.innerHTML = "";
  });

  elements.btnAutoscroll.addEventListener("click", () => {
    triggerHaptic("light");
    autoscroll = !autoscroll;
    elements.btnAutoscroll.textContent = `Auto-Scroll: ${autoscroll ? "ON" : "OFF"}`;
  });

  elements.terminalForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    triggerHaptic("medium");
    const cmd = elements.terminalCmdInput.value.trim();
    if (!cmd) return;

    appendTerminalLine(`> ${cmd}`, "var(--accent-cyan)");
    elements.terminalCmdInput.value = "";

    // Send via WebSocket if open, else fallback to REST
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "command", text: cmd }));
    } else {
      try {
        await apiFetch("/telegram/command", {
          method: "POST",
          body: JSON.stringify({ command: cmd }),
        });
      } catch (err) {
        appendTerminalLine(`[Error sending command: ${err}]`, "var(--accent-rose)");
      }
    }
  });

  function initWebSocket() {
    if (ws) {
      try { ws.close(); } catch (e) {}
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/mobile${authToken ? `?token=${encodeURIComponent(authToken)}` : ""}`;

    try {
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        elements.connectionStatus.className = "status-badge";
        elements.statusText.textContent = "LIVE";
        appendTerminalLine("[WebSocket Connected to AJA Kernel]", "var(--accent-emerald)");
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWsEvent(msg);
        } catch (err) {
          appendTerminalLine(event.data);
        }
      };

      ws.onclose = () => {
        elements.connectionStatus.className = "status-badge disconnected";
        elements.statusText.textContent = "DISCONNECTED";
        scheduleReconnect();
      };

      ws.onerror = () => {
        elements.connectionStatus.className = "status-badge disconnected";
        elements.statusText.textContent = "ERROR";
      };
    } catch (e) {
      scheduleReconnect();
    }
  }

  function handleWsEvent(msg) {
    if (msg.type === "state_update") {
      if (msg.data && msg.data.tasks) renderKanban(msg.data.tasks);
      if (msg.data && msg.data.approvals) renderApprovals(msg.data.approvals);
    } else if (msg.type === "terminal_output" || msg.type === "log") {
      appendTerminalLine(msg.text || msg.line || JSON.stringify(msg));
    } else if (msg.type === "approval_requested") {
      triggerHaptic("warning");
      loadApprovals();
      appendTerminalLine(`[APPROVAL REQUESTED] ${msg.command || ""}`, "var(--accent-amber)");
    } else if (msg.type === "task_update") {
      loadTasks();
    } else {
      appendTerminalLine(`[${msg.type || "event"}] ${JSON.stringify(msg.data || msg)}`);
    }
  }

  function scheduleReconnect() {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(() => {
      initWebSocket();
    }, 3000);
  }

  function escapeHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ── 9. Boot Lifecycle ─────────────────────────────────────────────────────
  loadTasks();
  loadApprovals();
  loadTelemetry();
  initWebSocket();

  // Periodic polling interval fallback
  setInterval(() => {
    const activeTab = document.querySelector(".tab-pane.active");
    if (activeTab && activeTab.id === "tab-kanban") loadTasks();
    if (activeTab && activeTab.id === "tab-approvals") loadApprovals();
  }, 10000);
})();
