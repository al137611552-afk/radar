"use strict";

const state = {
  data: null,
  panel: "overview",
  query: "",
  loading: false,
  productCode: "",
  productData: null,
  productLoading: false,
  productRequestId: 0,
};
const titles = {
  overview: "市场总览",
  intraday: "盘中成交额雷达",
  options: "临期期权信号",
  "option-history": "期权信号变化",
  momentum: "商品动量排名",
  sectors: "板块动量排名",
  history: "日频排名变化",
  product: "单品种研究详情",
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

function volatilityBadge(risk) {
  const kind = risk === "高波动" ? "high" : risk === "偏高" ? "elevated" : risk === "常态" ? "normal" : "unknown";
  return badge(value(risk), kind, "risk-badge");
}

function setPanel(panel) {
  state.panel = panel;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.panel === panel));
  $$(".panel").forEach((section) => section.classList.toggle("active", section.dataset.panel === panel));
  $("#page-title").textContent = titles[panel];
  if (panel === "overview") $("#global-search").value = "";
  if (panel === "overview") state.query = "";
  renderTables();
  if (panel === "product" && state.productCode && state.productData?.code !== state.productCode) {
    loadProductDetail(state.productCode);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderSummary() {
  const summary = state.data.summary;
  const cards = [
    ["盘中合约", summary.intraday_count, "完整5分钟横截面", "purple"],
    ["期权信号", summary.option_count, "临期双确认候选", "red"],
    ["期权变化", summary.option_history_count, "最近小时信号生命周期", "purple"],
    ["动量品种", summary.momentum_count, "多周期商品指数", "green"],
    ["动量板块", summary.sector_count, "等权板块强弱", "purple"],
    ["排名历史", summary.momentum_history_count, "最近交易日变化", "green"],
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
  const visibleRows = type === "momentum" ? directionalRows(rows, "risk_long_rank") : rows;
  target.replaceChildren(...visibleRows.slice(0, 4).map((row) => {
    const item = node("div", "signal-item");
    const top = node("div");
    top.append(node("strong", "", value(row.name, row.code)), node("span", "score", type === "option" ? value(row.signal_score) : number(row.risk_adjusted_score, 1)));
    const detail = type === "option"
      ? `${value(row.option_type)} · DTE ${value(row.dte)} · ${value(row.underlying)}`
      : `${value(row.sector)} · 20日波动 ${percent(row.annualized_volatility_20d)} · ${value(row.volatility_risk)}`;
    item.append(top, node("p", "", detail));
    return item;
  }));
}

function renderFreshness() {
  const labels = { intraday: "盘中雷达", options: "期权信号", momentum: "动量排名", sectors: "板块动量" };
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

function renderOptionHistory() {
  const rows = (state.data.option_history || []).filter(matches);
  $("#option-history-meta").textContent = `${rows.length} 合约`;
  const statusKind = (status) => {
    if (status === "新晋双确认" || status === "信号增强") return "long";
    if (status === "双确认失效" || status === "信号减弱" || status === "移出候选") return "short";
    return "flat";
  };
  $("#option-history-table").replaceChildren(...rows.map((item) => {
    const row = node("tr");
    addCell(row, value(item.scan_time), "number muted");
    const instrumentCell = node("td");
    const instrument = node("div", "instrument");
    instrument.append(node("strong", "", value(item.name, item.code)), node("small", "", `${value(item.code)} · ${value(item.exchange)}`));
    instrumentCell.append(instrument); row.append(instrumentCell);
    const statusCell = node("td"); statusCell.append(badge(value(item.change_status), statusKind(item.change_status))); row.append(statusCell);
    const typeCell = node("td"); typeCell.append(item.option_type === "PUT" ? badge("PUT", "put", "type-badge") : badge("CALL", "call", "type-badge")); row.append(typeCell);
    addCell(row, value(item.dte), "number");
    addCell(row, value(item.underlying));
    addCell(row, number(item.confirmation_score, 0), "score");
    const change = Number(item.confirmation_score_change);
    addCell(row, Number.isFinite(change) ? `${change > 0 ? "+" : ""}${change}` : "—", `number ${trendClass(item.confirmation_score_change)}`);
    const confirmedCell = node("td"); confirmedCell.append(badge(item.double_confirmed ? "是" : "否", item.double_confirmed ? "long" : "flat")); row.append(confirmedCell);
    addCell(row, number(item.last_price), "number");
    addCell(row, integer(item.recent_volume), "number");
    addCell(row, integer(item.open_interest), "number");
    addCell(row, value(item.bar_time), "number muted");
    return row;
  }));
}

function finiteRank(input) {
  if (input === null || input === undefined || input === "") return Number.POSITIVE_INFINITY;
  const parsed = Number(input);
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function directionalRows(rows, rankField) {
  return rows.slice().sort((left, right) => {
    const leftRank = finiteRank(left[rankField]);
    const rightRank = finiteRank(right[rankField]);
    return leftRank - rightRank || String(left.code ?? left.sector).localeCompare(String(right.code ?? right.sector));
  });
}

function momentumRow(item, rankField, primaryScoreField) {
  const row = node("tr"); addCell(row, String(item[rankField] ?? "-").padStart(2, "0"), "rank");
  const instrumentCell = node("td");
  const instrument = node("div", "instrument");
  const productButton = node("button", "product-link", value(item.name, item.code));
  productButton.type = "button";
  productButton.addEventListener("click", () => openProduct(item.code));
  instrument.append(productButton, node("small", "", value(item.code)));
  instrumentCell.append(instrument); row.append(instrumentCell);
  addCell(row, value(item.sector));
  const secondaryScoreField = primaryScoreField === "momentum_score" ? "risk_adjusted_score" : "momentum_score";
  addCell(row, number(item[primaryScoreField], 1), "score");
  addCell(row, number(item[secondaryScoreField], 1), "score");
  addCell(row, percent(item.annualized_volatility_20d), "number");
  const riskCell = node("td"); riskCell.append(volatilityBadge(item.volatility_risk)); row.append(riskCell);
  ["return_5d", "return_20d", "return_60d", "return_120d"].forEach((key) => addCell(row, percent(item[key]), `number ${trendClass(item[key])}`));
  addCell(row, value(item.exchange)); addCell(row, value(item.as_of), "number muted"); return row;
}

function renderMomentum() {
  const rows = state.data.momentum.filter(matches);
  $("#momentum-meta").textContent = `${rows.length} 品种 · 原始/风险调整四榜`;
  $("#momentum-long-table").replaceChildren(...directionalRows(rows, "long_rank").map((item) => momentumRow(item, "long_rank", "momentum_score")));
  $("#momentum-short-table").replaceChildren(...directionalRows(rows, "short_rank").map((item) => momentumRow(item, "short_rank", "momentum_score")));
  $("#momentum-risk-long-table").replaceChildren(...directionalRows(rows, "risk_long_rank").map((item) => momentumRow(item, "risk_long_rank", "risk_adjusted_score")));
  $("#momentum-risk-short-table").replaceChildren(...directionalRows(rows, "risk_short_rank").map((item) => momentumRow(item, "risk_short_rank", "risk_adjusted_score")));
}

function sectorRow(item, rankField, primaryScoreField) {
  const row = node("tr");
  addCell(row, String(item[rankField] ?? "-").padStart(2, "0"), "rank");
  addCell(row, value(item.sector));
  addCell(row, integer(item.constituents), "number");
  const secondaryScoreField = primaryScoreField === "sector_momentum_score" ? "sector_risk_adjusted_score" : "sector_momentum_score";
  addCell(row, number(item[primaryScoreField], 1), "score");
  addCell(row, number(item[secondaryScoreField], 1), "score");
  addCell(row, percent(item.sector_mean_annualized_volatility_20d), "number");
  const riskCell = node("td"); riskCell.append(volatilityBadge(item.sector_volatility_risk)); row.append(riskCell);
  ["sector_return_5d", "sector_return_20d", "sector_return_60d", "sector_return_120d"].forEach((key) => addCell(row, percent(item[key]), `number ${trendClass(item[key])}`));
  addCell(row, value(item.as_of), "number muted");
  return row;
}

function renderSectors() {
  const rows = state.data.sectors.filter(matches);
  $("#sectors-meta").textContent = `${rows.length} 板块 · 原始/风险调整四榜`;
  $("#sectors-long-table").replaceChildren(...directionalRows(rows, "sector_long_rank").map((item) => sectorRow(item, "sector_long_rank", "sector_momentum_score")));
  $("#sectors-short-table").replaceChildren(...directionalRows(rows, "sector_short_rank").map((item) => sectorRow(item, "sector_short_rank", "sector_momentum_score")));
  $("#sectors-risk-long-table").replaceChildren(...directionalRows(rows, "sector_risk_long_rank").map((item) => sectorRow(item, "sector_risk_long_rank", "sector_risk_adjusted_score")));
  $("#sectors-risk-short-table").replaceChildren(...directionalRows(rows, "sector_risk_short_rank").map((item) => sectorRow(item, "sector_risk_short_rank", "sector_risk_adjusted_score")));
}

function renderMomentumHistory() {
  const rows = (state.data.momentum_history || []).filter(matches);
  $("#momentum-history-meta").textContent = `${rows.length} 品种`;
  const rankDelta = (input) => {
    if (input === null || input === undefined || input === "") return "—";
    const parsed = Number(input);
    return Number.isFinite(parsed) ? `${parsed > 0 ? "+" : ""}${parsed}` : "—";
  };
  $("#momentum-history-table").replaceChildren(...rows.map((item) => {
    const row = node("tr");
    addCell(row, value(item.snapshot_date), "number muted");
    const instrumentCell = node("td");
    const instrument = node("div", "instrument");
    instrument.append(node("strong", "", value(item.name, item.code)), node("small", "", value(item.code)));
    instrumentCell.append(instrument); row.append(instrumentCell);
    addCell(row, value(item.sector));
    addCell(row, integer(item.long_rank), "rank");
    addCell(row, rankDelta(item.long_rank_change), `number ${trendClass(item.long_rank_change)}`);
    addCell(row, integer(item.risk_long_rank), "rank");
    addCell(row, rankDelta(item.risk_long_rank_change), `number ${trendClass(item.risk_long_rank_change)}`);
    const statusCell = node("td");
    const isNew = item.new_long_entry || item.new_risk_long_entry;
    statusCell.append(badge(isNew ? "新晋" : "持续", isNew ? "long" : "flat")); row.append(statusCell);
    addCell(row, number(item.momentum_score, 1), "score");
    addCell(row, number(item.risk_adjusted_score, 1), "score");
    return row;
  }));
}

function svgNode(tag, attributes = {}, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([name, content]) => element.setAttribute(name, String(content)));
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function renderRankChart(rows) {
  const svg = $("#product-rank-chart");
  const empty = $("#product-chart-empty");
  const hasRank = (input) => input !== null && input !== "" && Number.isFinite(Number(input));
  const valid = rows.filter((row) => hasRank(row.long_rank) || hasRank(row.risk_long_rank));
  svg.replaceChildren();
  empty.classList.toggle("hidden", valid.length > 0);
  svg.classList.toggle("visible", valid.length > 0);
  svg.classList.toggle("single-point", valid.length === 1);
  svg.closest(".product-chart-card").classList.toggle("single-snapshot", valid.length === 1);
  $("#product-chart-meta").textContent = valid.length === 1
    ? "1 个交易日 · 等待后续快照"
    : valid.length ? `${valid.length} 个交易日` : "暂无排名历史";
  if (!valid.length) return;

  const width = 900; const height = 280;
  const left = 54; const right = 20; const top = 24; const bottom = 42;
  const plotWidth = width - left - right; const plotHeight = height - top - bottom;
  const ranks = valid.flatMap((row) => [row.long_rank, row.risk_long_rank]).filter(hasRank).map(Number);
  const maxRank = Math.max(2, ...ranks);
  const x = (index) => left + (valid.length === 1 ? plotWidth / 2 : (index * plotWidth) / (valid.length - 1));
  const y = (rank) => top + ((Math.max(1, rank) - 1) * plotHeight) / (maxRank - 1);
  const ticks = [...new Set([1, Math.ceil(maxRank / 2), maxRank])];
  ticks.forEach((rank) => {
    const lineY = y(rank);
    svg.append(
      svgNode("line", { x1: left, y1: lineY, x2: width - right, y2: lineY, class: "chart-grid" }),
      svgNode("text", { x: left - 12, y: lineY + 4, class: "chart-axis", "text-anchor": "end" }, rank),
    );
  });
  const pointsFor = (field) => valid.map((row, index) => {
    const rank = Number(row[field]);
    return Number.isFinite(rank) && row[field] !== null && row[field] !== ""
      ? { x: x(index), y: y(rank) }
      : null;
  }).filter(Boolean);
  [["long_rank", "raw-line"], ["risk_long_rank", "risk-line"]].forEach(([field, className]) => {
    const points = pointsFor(field);
    if (!points.length) return;
    svg.append(svgNode("path", { d: points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" "), class: `rank-line ${className}` }));
    const latest = points[points.length - 1];
    svg.append(svgNode("circle", { cx: latest.x, cy: latest.y, r: 5, class: `rank-dot ${className}` }));
  });
  [...new Set([0, Math.floor((valid.length - 1) / 2), valid.length - 1])].forEach((index) => {
    svg.append(svgNode("text", { x: x(index), y: height - 13, class: "chart-axis", "text-anchor": index === 0 ? "start" : index === valid.length - 1 ? "end" : "middle" }, value(valid[index].snapshot_date, "")));
  });
}

function renderProductDetail() {
  const detail = state.productData;
  const current = detail?.current;
  if (!detail || !current) {
    $("#product-name").textContent = state.productLoading ? "正在载入品种详情…" : "暂无该品种数据";
    $("#product-subtitle").textContent = state.productCode || "请选择一个动量品种";
    $("#product-risk").className = "risk-badge unknown";
    $("#product-risk").textContent = "—";
    $("#product-metrics").replaceChildren();
    renderRankChart(detail?.momentum_trajectory || []);
    $("#product-intraday-table").replaceChildren();
    $("#product-options-table").replaceChildren();
    $("#product-intraday-meta").textContent = "0 合约";
    $("#product-options-meta").textContent = "0 信号";
    return;
  }
  $("#product-name").textContent = value(current.name, current.code);
  $("#product-subtitle").textContent = `${value(current.code)} · ${value(current.exchange)} · ${value(current.sector)} · ${value(current.as_of)}`;
  const risk = $("#product-risk");
  risk.className = `risk-badge ${current.volatility_risk === "高波动" ? "high" : current.volatility_risk === "偏高" ? "elevated" : current.volatility_risk === "常态" ? "normal" : "unknown"}`;
  risk.textContent = value(current.volatility_risk);
  const metrics = [
    ["原始动量分", number(current.momentum_score, 1), "MOMENTUM"],
    ["风险调整分", number(current.risk_adjusted_score, 1), "RISK ADJUSTED"],
    ["原始多头排名", integer(current.long_rank), "RAW LONG"],
    ["风险多头排名", integer(current.risk_long_rank), "RISK LONG"],
    ["20日收益", percent(current.return_20d), "RETURN 20D"],
    ["20日年化波动", percent(current.annualized_volatility_20d), "VOLATILITY"],
  ];
  $("#product-metrics").replaceChildren(...metrics.map(([label, amount, caption]) => {
    const card = node("article", "metric-card product-metric");
    card.append(node("span", "metric-label", label), node("strong", "", amount), node("small", "", caption));
    return card;
  }));
  renderRankChart(detail.momentum_trajectory || []);

  const intraday = detail.intraday || [];
  $("#product-intraday-meta").textContent = `${intraday.length} 合约`;
  $("#product-intraday-table").replaceChildren(...intraday.map((item) => {
    const row = node("tr");
    addCell(row, `${value(item.name, item.code)} · ${value(item.code)}`);
    const sideCell = node("td"); sideCell.append(item.side === "多" ? badge("多头", "long") : item.side === "空" ? badge("空头", "short") : badge("盘整", "flat")); row.append(sideCell);
    addCell(row, integer(item.rank_15m), "rank"); addCell(row, number(item.close), "number"); addCell(row, `${number(item.turnover_15m_yi)} 亿`, "number"); addCell(row, percent(item.price_change_15m_pct), `number ${trendClass(item.price_change_15m_pct)}`); addCell(row, value(item.bar_time), "number muted");
    return row;
  }));

  const options = detail.options || [];
  $("#product-options-meta").textContent = `${options.length} 信号`;
  $("#product-options-table").replaceChildren(...options.map((item) => {
    const row = node("tr"); addCell(row, `${value(item.name, item.code)} · ${value(item.code)}`);
    const typeCell = node("td"); typeCell.append(item.option_type === "PUT" ? badge("PUT", "put", "type-badge") : badge("CALL", "call", "type-badge")); row.append(typeCell);
    addCell(row, value(item.dte), "number"); addCell(row, value(item.underlying)); addCell(row, number(item.strike), "number"); addCell(row, number(item.confirmation_score, 0), "score"); addCell(row, integer(item.recent_volume), "number"); addCell(row, value(item.bar_time), "number muted");
    return row;
  }));
}

async function loadProductDetail(code) {
  if (!code) return;
  const requestId = ++state.productRequestId;
  state.productCode = code;
  state.productLoading = true;
  state.productData = null;
  renderProductDetail();
  try {
    const response = await fetch(`/api/product?code=${encodeURIComponent(code)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const detail = await response.json();
    if (state.productRequestId === requestId) state.productData = detail;
  } catch (error) {
    if (state.productRequestId === requestId) toast(`品种详情载入失败：${error.message}`);
  } finally {
    if (state.productRequestId === requestId) {
      state.productLoading = false;
      renderProductDetail();
    }
  }
}

function openProduct(code) {
  if (!code) return;
  state.productCode = code;
  $("#product-select").value = code;
  setPanel("product");
}

function populateProductSelect() {
  const rows = directionalRows(state.data.momentum || [], "risk_long_rank");
  const select = $("#product-select");
  const options = rows.map((item) => node("option", "", `${value(item.name, item.code)} · ${value(item.code)}`));
  options.forEach((option, index) => { option.value = rows[index].code; });
  select.replaceChildren(...(options.length ? options : [node("option", "", "暂无动量品种")]));
  if (!options.length) return;
  const selected = rows.some((item) => item.code === state.productCode) ? state.productCode : rows[0].code;
  state.productCode = selected;
  select.value = selected;
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
  renderIntraday(); renderOptions(); renderOptionHistory(); renderMomentum(); renderSectors(); renderMomentumHistory(); renderTasks();
}

function renderAll() {
  renderSummary();
  compactIntraday(state.data.intraday);
  overviewSignals("#overview-options", state.data.options, "option");
  overviewSignals("#overview-momentum", state.data.momentum, "momentum");
  renderFreshness();
  populateProductSelect();
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
    if (state.panel === "product" && state.productCode) loadProductDetail(state.productCode);
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
$("#product-select").addEventListener("change", (event) => openProduct(event.target.value));

tickClock();
refreshData();
setInterval(tickClock, 1000);
setInterval(refreshData, 30000);
