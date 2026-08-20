const state = {
  customer: null,
  activeTab: "history",
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
  renderConversation(customer.history);
  renderDetail(customer);
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
    role.textContent = turn.role;
    const content = document.createElement("span");
    content.textContent = turn.content;
    bubble.append(role, content);
    row.append(bubble);
    log.append(row);
  }
  log.scrollTop = log.scrollHeight;
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
    rows = customer.history.map((turn, index) =>
      detailRow(`${index + 1} · ${turn.role}`, turn.content)
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

$("#followup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  setBusy(button, true);
  try {
    const payload = await request("/api/followups", {
      method: "POST",
      body: JSON.stringify({
        customer_id: customerId(),
        delay_seconds: Number($("#followup-delay").value),
        context: $("#followup-context").value,
      }),
    });
    state.customer = payload.customer;
    render();
    notice(payload.execution.executed ? "跟进已标记，本轮未回复" : `未标记 · ${payload.execution.reason}`);
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

$("#queue-script").addEventListener("click", async () => {
  try {
    const payload = await request("/api/script", {
      method: "POST",
      body: JSON.stringify({
        intent: $("#fake-intent").value,
        dissatisfied: $("#fake-dissatisfied").checked,
      }),
    });
    notice(`已预排 · ${payload.queued.intent}`);
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
