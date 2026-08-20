const state = {
  customer: null,
  activeTab: "history",
  pendingRefreshTimer: null,
};

const $ = (selector) => document.querySelector(selector);
const customerId = () => $("#customer-id").value.trim();

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function notice(message, error = false) {
  const element = $("#notice");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("show");
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => element.classList.remove("show"), 2800);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

async function loadMeta() {
  try {
    const meta = await request("/api/meta");
    $("#mode-label").textContent = meta.mode;
    $("#connection-dot").classList.add("ready");
    $("#fake-controls").hidden = !meta.fake;
  } catch (error) {
    $("#mode-label").textContent = "offline";
    $("#connection-dot").classList.add("error");
    notice(error.message, true);
  }
}

async function refreshCustomer() {
  const id = customerId();
  if (!id) return notice("Customer ID 不能为空", true);
  try {
    state.customer = await request(`/api/customer?customer_id=${encodeURIComponent(id)}`);
    render();
  } catch (error) {
    notice(error.message, true);
  }
}

function render() {
  const customer = state.customer;
  if (!customer) return;
  $("#session-value").textContent = customer.state.session;
  $("#streak-value").textContent = customer.state.anomaly_count;
  $("#intent-value").textContent = customer.state.last_estimation?.intent || "none";
  $("#pending-value").textContent = customer.followups.length;
  $("#handoff-status").hidden = customer.state.session !== "escalated";
  renderConversation([
    ...customer.history,
    ...(customer.buffered_messages || []).map((turn) => ({ ...turn, buffered: true })),
  ]);
  renderDetail(customer);
  schedulePendingRefresh(customer);
}

function renderConversation(history) {
  const log = $("#conversation-log");
  log.replaceChildren();
  if (!history.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无会话";
    log.append(empty);
    return;
  }
  for (const turn of history) {
    const row = document.createElement("div");
    row.className = `turn ${turn.role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const role = document.createElement("small");
    role.textContent = turn.buffered ? `${turn.role} · 待聚合` : turn.role;
    const content = document.createElement("span");
    content.textContent = turn.content;
    bubble.append(role, content);
    row.append(bubble);
    log.append(row);
  }
  log.scrollTop = log.scrollHeight;
}

function schedulePendingRefresh(customer) {
  clearTimeout(state.pendingRefreshTimer);
  const buffer = customer.reply_buffer;
  if (!buffer?.buffered) return;
  state.pendingRefreshTimer = setTimeout(
    refreshCustomer,
    Math.max(250, (buffer.due_in_seconds + 0.25) * 1000)
  );
}

function detailRow(title, text) {
  const row = document.createElement("div");
  row.className = "detail-row";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = text;
  row.append(heading, body);
  return row;
}

function renderDetail(customer) {
  const panel = $("#detail-panel");
  const list = document.createElement("div");
  list.className = "detail-list";
  let rows = [];
  if (state.activeTab === "history") {
    const turns = [
      ...customer.history,
      ...(customer.buffered_messages || []).map((turn) => ({ ...turn, buffered: true })),
    ];
    rows = turns.map((turn, index) =>
      detailRow(
        `${index + 1} · ${turn.role}${turn.buffered ? " · 待聚合" : ""}`,
        turn.content
      )
    );
  } else if (state.activeTab === "followups") {
    rows = customer.followups.map((item, index) =>
      detailRow(
        `${index + 1} · ${Math.ceil(item.due_in_seconds)}s`,
        item.context || "no context"
      )
    );
  } else {
    rows = customer.audit.map((item, index) =>
      detailRow(`${index + 1} · ${item.event}`, item.detail || "no detail")
    );
  }
  if (!rows.length) rows = [detailRow("Empty", "暂无记录")];
  list.append(...rows);
  panel.replaceChildren(list);
}

$("#message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setBusy(button, true);
  try {
    const payload = await request("/api/messages", {
      method: "POST",
      body: JSON.stringify({ customer_id: customerId(), message: $("#message-input").value }),
    });
    if (payload.customer) {
      state.customer = payload.customer;
      render();
    }
    const result = payload.result;
    if (result.note === "invalid_input") notice(`输入无效 · ${result.input_error}`, true);
    else if (result.transition.escalated_now) notice("已转人工 · 自动处理已静默");
    else if (result.note === "buffered") {
      const aggregation = payload.aggregation;
      notice(
        `已加入聚合 · 共 ${aggregation.pending_count} 条 · 约 ${Math.ceil(aggregation.due_in_seconds)}s 后处理`
      );
    }
    else if (result.note.startsWith("silent")) notice("会话处于终态，本轮保持静默");
    else if (result.note === "fail_closed") notice("模型调用失败，本轮未执行动作", true);
    else notice(result.executions.some((item) => item.executed) ? "处理完成" : "本轮未执行动作");
    $("#message-input").value = "";
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(button, false);
  }
});

$("#run-followups").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  try {
    const payload = await request("/api/followups/run", { method: "POST", body: "{}" });
    notice(payload.outcomes.length ? `处理 ${payload.outcomes.length} 条到期跟进` : "没有到期跟进");
    await refreshCustomer();
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(event.currentTarget, false);
  }
});

$("#reactivate").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  try {
    state.customer = await request("/api/reactivate", {
      method: "POST",
      body: JSON.stringify({ customer_id: customerId() }),
    });
    render();
    notice("客户已由人工重新激活");
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(event.currentTarget, false);
  }
});

$("#reset-session").addEventListener("click", async (event) => {
  setBusy(event.currentTarget, true);
  try {
    state.customer = await request("/api/reset", {
      method: "POST",
      body: JSON.stringify({ customer_id: customerId() }),
    });
    render();
    $("#message-input").value = "";
    notice("已新建空白会话");
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(event.currentTarget, false);
  }
});

$("#queue-script").addEventListener("click", async () => {
  try {
    const payload = await request("/api/script", {
      method: "POST",
      body: JSON.stringify({
        intent: $("#fake-intent").value,
        dissatisfied: $("#fake-dissatisfied").checked,
        followup_requested: $("#fake-followup").checked,
      }),
    });
    notice(
      payload.queued.followup_requested
        ? `已预排 · ${payload.queued.intent} · 稍后跟进`
        : `已预排 · ${payload.queued.intent}`
    );
  } catch (error) {
    notice(error.message, true);
  }
});

$("#queue-error").addEventListener("click", async () => {
  try {
    await request("/api/script", { method: "POST", body: JSON.stringify({ error: true }) });
    notice("已预排模型失败");
  } catch (error) {
    notice(error.message, true);
  }
});

$("#refresh").addEventListener("click", refreshCustomer);
$("#customer-id").addEventListener("change", refreshCustomer);
document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    if (state.customer) renderDetail(state.customer);
  });
});

loadMeta().then(refreshCustomer);
