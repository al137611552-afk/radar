"use strict";

const state = { data: null, panel: "overview", query: "", loading: false };
const titles = {
  overview: "市场总览",
  intraday: "盘中成交额雷达",
  options: "临期期权信号",
  momentum: "商品动量排名",
  tasks: "自动任务状态",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function value(input, fallback = "—") {
  return input === null || input === undefined || input === "" ? fallback : input;
}

function number(input, digits = 2) {
  const parsed = Number(input);
  if (!Number.isFinite(parsed)) return "—";
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(parsed);
}

function integer(input) {
  const parsed = Number(input);
  return Number.isFinite(parsed) ? new Intl.NumberFormat("zh-CN").format(parsed) : "—";
}

function percent(input) {
  const parsed = Number(input);
  if (!Number.isFinite(parsed)) return "—";
  return `${parsed > 0 ? "+" : ""}${number(parsed, 2)}%`;
}

function trendClass(input) {
  const parsed = Number(input);
  return !Number.isFinite(parsed) || parsed === 0 ? "neutral" : parsed > 0 ? "positive" : "negative";
}

function age(seconds) {
  const parsed = Number(seconds);
  if (!Number.isFinite(parsed)) return "尚无文件";
  if (parsed < 60) return `${Math.floor(parsed)} 秒前`;
  if (parsed < 3600) return `${Math.floor(parsed / 60)} 分钟前`;
  if (parsed < 86400) return `${Math.floor(parsed / 3600)} 小时前`;
  return `${Math.floor(parsed / 86400)} 天前`;
}

function matches(row) {
  if (!state.query) return true;
  return Object.values(row).some((item) =>
    String(item ?? "").toLowerCase().includes(state.query)
  );
}

function badge(text, kind, base = "side-badge") {
  return node("span", `${base} ${kind}`, text);
}

function setPanel(panel) {
  state.panel = panel;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.panel === panel));
  $$(".panel").forEach((section) => section.classList.toggle("active", section.dataset.panel === panel));
  $("#page-title").textContent = titles[panel];
  if (panel === "overview") $("#global-search").value = "";
  if (panel === "overview") state.query = "";
  renderTables();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderSummary() {
  const summary = state.data.summary;
  const cards = [
    ["盘中合约", summary.intraday_count, "完整5分钟横截面", "purple"],
    ["期权信号", summary.option_count, "临期双确认候选", "red"],
    ["动量品种", summary.momentum_count, "多周期商品指数", "green"],
    ["失败任务", summary.failed_tasks, summary.health === "healthy" ? "调度链路正常" : "需要检查日志", summary.failed_tasks ? "red" : "green"],
  ];
  const target = $("#summary-cards");
  target.replaceChildren(...cards.map(([label, amount, caption, tone]) => {
    const card = node("article", "metric-card");
    const heading = node("div", "metric-label");
    heading.append(node("span", "", label), node("i", tone));
    card.append(heading, node("strong", "", integer(amount)), node("small", "", caption));
    return card;
  }));
  $("#hero-copy").textContent = summary.health === "degraded"
    ? summary.failed_tasks
      ? `发现 ${summary.failed_tasks} 项任务异常，请查看任务状态。`
      : "部分行情文件暂时不可读，其他可用数据仍正常展示。"
    : summary.health === "healthy" ? "行情产物已接入，调度链路当前无失败记录。" : "等待首次行情与调度任务生成数据。";
}

function compactIntraday(rows) {
  const target = $("#overview-intraday");
  if (!rows.length) {
    target.className = "compact-list empty-state";
    target.textContent = "等待盘中雷达数据";
    return;
  }
  target.className = "compact-list";
  target.replaceChildren(...rows.slice(0, 6).map((row, index) => {
    const line = node("div", "compact-row");
    const instrument = node("div", "instrument");
    instrument.append(node("strong", "", value(row.name, row.code)), node("small", "", `${value(row.code)} · ${value(row.exchange)}`));
    const side = row.side === "多" ? badge("多头", "long") : row.side === "空" ? badge("空头", "short") : badge("盘整", "flat");
    line.append(
      node("span", "rank", String(row.rank_15m ?? index + 1).padStart(2, "0")),
      instrument,
      node("span", "number", `${number(row.turnover_15m_yi)} 亿`),
      node("span", `number ${trendClass(row.price_change_15m_pct)}`, percent(row.price_change_15m_pct)),
      side,
    );
    return line;
  }));
}

function overviewSignals(targetSelector, rows, type) {
  const target = $(targetSelector);
  if (!rows.length) {
    target.className = "signal-stack empty-state";
    target.textContent = type === "option" ? "等待期权信号" : "等待动量排名";
    return;
  }
  target.className = "signal-stack";
  target.replaceChildren(...rows.slice(0, 4).map((row) => {
    const item = node("div", "signal-item");
    const top = node("div");
    top.append(node("strong", "", value(row.name, row.code)), node("span", "score", type === "option" ? value(row.signal_score) : number(row.momentum_score, 1)));
    const detail = type === "option"
      ? `${value(row.option_type)} · DTE ${value(row.dte)} · ${value(row.underlying)}`
      : `5日 ${percent(row.return_5d)} · 20日 ${percent(row.return_20d)}`;
    item.append(top, node("p", "", detail));
    return item;
  }));
}

function renderFreshness() {
  const labels = { intraday: "盘中雷达", options: "期权信号", momentum: "动量排名" };
  $("#freshness-grid").replaceChildren(...Object.entries(state.data.files).map(([key, file]) => {
    const item = node("div", "fresh-item");
    const status = !file.exists ? "尚无文件" : !file.available ? "读取失败" : age(file.age_seconds);
    const detail = file.error || (file.exists ? `${file.rows} 行 · ${file.path}` : `${file.path} 尚未生成`);
    item.append(node("span", "", labels[key]), node("strong", "", status), node("small", "", detail));
    return item;
  }));
  $("#updated-at").textContent = `面板刷新 ${new Date(state.data.generated_at).toLocaleTimeString("zh-CN", { hour12: false })}`;
}

function addCell(row, text, className = "") {
  row.append(node("td", className, text));
}

function renderIntraday() {
  const rows = state.data.intraday.filter(matches);
  $("#intraday-meta").textContent = `${rows.length} 合约`;
  const body = $("#intraday-table");
  body.replaceChildren(...rows.map((item, index) => {
    const row = node("tr");
    addCell(row, String(item.rank_15m ?? index + 1).padStart(2, "0"), "rank");
    const instrumentCell = node("td");
    const instrument = node("div", "instrument");
    instrument.append(node("strong", "", value(item.name, item.code)), node("small", "", `${value(item.code)} · ${value(item.exchange)}`));
    instrumentCell.append(instrument); row.append(instrumentCell);
    const sideCell = node("td"); sideCell.append(item.side === "多" ? badge("多头", "long") : item.side === "空" ? badge("空头", "short") : badge("盘整", "flat")); row.append(sideCell);
    addCell(row, number(item.close), "number");
    addCell(row, `${number(item.turnover_15m_yi)} 亿`, "number");
    addCell(row, percent(item.price_change_15m_pct), `number ${trendClass(item.price_change_15m_pct)}`);
    addCell(row, percent(item.turnover_acceleration_15m_pct), `number ${trendClass(item.turnover_acceleration_15m_pct)}`);
    addCell(row, integer(item.oi_change_15m), `number ${trendClass(item.oi_change_15m)}`);
    addCell(row, value(item.bar_time), "number muted");
    return row;
  }));
}

function renderOptions() {
  const rows = state.data.options.filter(matches);
  $("#options-meta").textContent = `${rows.length} 信号`;
  $("#options-table").replaceChildren(...rows.map((item) => {
    const row = node("tr");
    const instrumentCell = node("td");
    const instrument = node("div", "instrument");
    instrument.append(node("strong", "", value(item.name, item.code)), node("small", "", `${value(item.code)} · ${value(item.exchange)}`)); instrumentCell.append(instrument); row.append(instrumentCell);
    const typeCell = node("td"); typeCell.append(item.option_type === "PUT" ? badge("PUT", "put", "type-badge") : badge("CALL", "call", "type-badge")); row.append(typeCell);
    addCell(row, value(item.dte), "number"); addCell(row, value(item.underlying)); addCell(row, number(item.strike), "number"); addCell(row, number(item.last_price), "number"); addCell(row, `${value(item.signal_score)} / ${value(item.confirmation_score)}`, "score"); addCell(row, integer(item.recent_volume), "number"); addCell(row, integer(item.open_interest), "number"); addCell(row, value(item.bar_time), "number muted");
    return row;
  }));
}

function renderMomentum() {
  const rows = state.data.momentum.filter(matches);
  $("#momentum-meta").textContent = `${rows.length} 品种`;
  $("#momentum-table").replaceChildren(...rows.map((item, index) => {
    const row = node("tr"); addCell(row, String(index + 1).padStart(2, "0"), "rank");
    const instrumentCell = node("td"); const instrument = node("div", "instrument"); instrument.append(node("strong", "", value(item.name, item.code)), node("small", "", value(item.code))); instrumentCell.append(instrument); row.append(instrumentCell);
    addCell(row, number(item.momentum_score, 1), "score");
    ["return_5d", "return_20d", "return_60d", "return_120d"].forEach((key) => addCell(row, percent(item[key]), `number ${trendClass(item[key])}`));
    addCell(row, value(item.exchange)); addCell(row, value(item.as_of), "number muted"); return row;
  }));
}

function renderTasks() {
  const target = $("#task-grid");
  const rows = state.data.tasks.filter(matches);
  if (!rows.length) { target.className = "task-grid empty-state"; target.textContent = "尚无任务运行记录"; return; }
  target.className = "task-grid";
  target.replaceChildren(...rows.map((item) => {
    const card = node("article", `task-card ${item.status}`);
    card.append(badge(item.status.toUpperCase(), item.status, "status-badge"), node("h3", "", item.task), node("p", "", value(item.slot)));
    const meta = node("div", "task-meta");
    [["Attempt", item.attempt], ["开始", item.started_at], ["完成", item.finished_at], ["最近成功", item.last_success_at]].forEach(([label, content]) => { const block = node("div"); block.append(node("span", "", label), node("strong", "", value(content))); meta.append(block); });
    card.append(meta); if (item.error) card.append(node("p", "task-error", item.error)); return card;
  }));
}

function renderTables() {
  if (!state.data) return;
  renderIntraday(); renderOptions(); renderMomentum(); renderTasks();
}

function renderAll() {
  renderSummary();
  compactIntraday(state.data.intraday);
  overviewSignals("#overview-options", state.data.options, "option");
  overviewSignals("#overview-momentum", state.data.momentum, "momentum");
  renderFreshness();
  renderTables();
}

function toast(message) {
  const element = $("#toast"); element.textContent = message; element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 2200);
}

async function refreshData(manual = false) {
  if (state.loading) return;
  state.loading = true; $("#refresh-button").classList.add("loading");
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    renderAll();
    $("#connection-dot").className = "online"; $("#connection-text").textContent = "数据已连接";
    if (manual) toast("数据已刷新");
  } catch (error) {
    $("#connection-dot").className = "offline"; $("#connection-text").textContent = "连接异常";
    toast(`刷新失败：${error.message}`);
  } finally {
    state.loading = false; $("#refresh-button").classList.remove("loading");
  }
}

function tickClock() {
  const now = new Date();
  $("#market-time").textContent = now.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
  $("#market-date").textContent = now.toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => setPanel(button.dataset.panel)));
$$("[data-open-panel]").forEach((button) => button.addEventListener("click", () => setPanel(button.dataset.openPanel)));
$("#refresh-button").addEventListener("click", () => refreshData(true));
$("#global-search").addEventListener("input", (event) => { state.query = event.target.value.trim().toLowerCase(); renderTables(); });

tickClock();
refreshData();
setInterval(tickClock, 1000);
setInterval(refreshData, 30000);
