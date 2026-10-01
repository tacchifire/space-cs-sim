"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const metricDefaults = {
    temperature_c: {label: "温度", unit: "°C"}, pressure_pa: {label: "気圧", unit: "Pa"},
    humidity_percent: {label: "湿度", unit: "%"}, accel_x_mg: {label: "加速度 X", unit: "mg"},
    accel_y_mg: {label: "加速度 Y", unit: "mg"}, accel_z_mg: {label: "加速度 Z", unit: "mg"},
    acceleration_norm_mg: {label: "加速度の大きさ（重力を含む）", unit: "mg"}
  };
  const views = {
    connection: ["接続", "接続したボードとポートの状態を確認します。"],
    measurement: ["計測", "センサーの読み出しと、受信データの記録を行います。"],
    captures: ["保存ログ", "実験の記録を振り返り、数値とイベントを確認します。"],
    exercises: ["演習", "シミュレーションの課題と実行手順を確認します。"]
  };
  const tasks = {info: "ボード情報の取得", watch: "センサー計測", monitor: "受信データの記録"};
  const modes = {info: "情報取得", watch: "センサー", monitor: "受信記録"};
  const statuses = {running: "実行中", completed: "完了", failed: "失敗"};
  const transitions = {triggered: "範囲外", recovered: "回復", unavailable: "欠測", resumed: "取得再開"};
  let state = null, online = false, pending = false, pendingJob = false, view = "connection", selectedCapture = null;
  let metrics = metricDefaults, loadedCapture = null, currentMetric = "temperature_c";
  let timer = null, controller = null, toastTimer = null, captureRequest = 0, ownJob = null, stopped = false, chartResizeFrame = 0;
  let devicesSignature = "", capturesSignature = "", exercisesLoaded = false;
  const selectionKey = "cuberange.flatsat.capture";
  let storedSelection = null, selectionRestored = false;
  try {
    const saved = JSON.parse(sessionStorage.getItem(selectionKey));
    if (/^[0-9a-f]{32}$/.test(saved?.id || "")) storedSelection = saved;
  } catch (_) { /* Storage can be unavailable in a private browser session. */ }
  const knownJobs = new Map();

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function number(value, digits = 2) {
    if (!finite(value)) return "—";
    if (value !== 0 && (Math.abs(value) >= 1e7 || Math.abs(value) < .001)) return value.toExponential(2);
    return value.toLocaleString("ja-JP", {maximumFractionDigits: digits});
  }
  function time(value, full = false) {
    if (typeof value !== "string") return "時刻未記録";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "時刻未記録";
    return date.toLocaleString("ja-JP", full
      ? {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}
      : {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
  }
  function badge(text, kind = "neutral") { return el("span", `badge badge-${kind}`, text); }
  function toast(message, error = false) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").className = `toast${error ? " error" : ""}`;
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
  }
  function showError(id, message) {
    const target = $(id);
    target.textContent = message || "";
    target.hidden = !message;
  }
  function switchView(next, updateHash = true) {
    if (!Object.hasOwn(views, next)) next = "connection";
    const changed = view !== next;
    view = next;
    for (const key of Object.keys(views)) $("view-" + key).hidden = key !== next;
    for (const button of document.querySelectorAll(".navigation [data-view]")) {
      if (button.dataset.view === next) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    $("page-title").textContent = views[next][0];
    $("page-subtitle").textContent = views[next][1];
    if (updateHash && location.hash !== "#" + next) history.replaceState(null, "", "#" + next);
    if (next === "exercises" && !exercisesLoaded) loadExercises();
    if (next === "captures") {
      restoreSelection();
      if (loadedCapture?.samples?.length && $("capture-chart")) renderMetric(loadedCapture);
    }
    if (changed && window.matchMedia("(max-width: 650px)").matches) window.scrollTo({top: 0, behavior: "auto"});
  }

  function saveSelection() {
    try { sessionStorage.setItem(selectionKey, JSON.stringify({id: selectedCapture, metric: currentMetric})); }
    catch (_) { /* The console remains usable without session storage. */ }
  }
  function restoreSelection() {
    if (selectionRestored || !state || view !== "captures") return;
    selectionRestored = true;
    if (storedSelection && state.captures.some((capture) => capture.id === storedSelection.id)) {
      if (Object.hasOwn(metrics, storedSelection.metric)) currentMetric = storedSelection.metric;
      selectCapture(storedSelection.id);
    }
  }

  async function api(path, body) {
    const options = {credentials: "same-origin", cache: "no-store"};
    if (body !== undefined) {
      if (!online || !state?.csrf_token) throw new Error("コンソールに接続できません。接続の復旧後にもう一度操作してください。");
      options.method = "POST";
      options.headers = {"Content-Type": "application/json", "X-CubeRange-Token": state.csrf_token};
      options.body = JSON.stringify(body);
    }
    const requestController = new AbortController();
    options.signal = requestController.signal;
    const deadline = setTimeout(() => requestController.abort(), 10000);
    try {
      const response = await fetch(path, options);
      let result;
      try { result = await response.json(); } catch (error) {
        if (error.name === "AbortError") throw error;
        throw new Error("応答を読み取れませんでした。コンソールの接続を確認してください。");
      }
      if (!response.ok) throw new Error(result.error || `操作を完了できませんでした（${response.status}）。`);
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("応答を 10 秒以内に確認できませんでした。現在の操作状況を確認してください。");
      throw error;
    } finally { clearTimeout(deadline); }
  }

  function boards() {
    const grouped = new Map();
    for (const port of state?.devices || []) {
      const id = port.board_id || port.serial_number || port.path;
      if (!grouped.has(id)) grouped.set(id, {id, serial: port.serial_number, ports: []});
      grouped.get(id).ports.push(port);
    }
    return [...grouped.values()];
  }
  function selectedBoard(selectId) { return boards().find((board) => board.id === $(selectId).value); }
  function shellReady(board) { return !!board?.ports.some((port) => port.role === "shell" && port.accessible); }
  function monitorReady() {
    const board = selectedBoard("monitor-board"), role = $("monitor-port").value;
    if (!board) return false;
    const ports = board.ports.filter((port) => ["shell", "radio0", "radio1"].includes(port.role) && (role === "all" || port.role === role));
    return ports.length > 0 && ports.every((port) => port.accessible);
  }
  function updateActions() {
    const busy = pending || !!state?.active_job;
    $("info-button").disabled = !online || busy || !shellReady(selectedBoard("info-board"));
    $("watch-button").disabled = !online || busy || !shellReady(selectedBoard("watch-board"));
    $("monitor-button").disabled = !online || busy || !monitorReady();
    $("capture-import").disabled = !online || pending;
    $("operation-status").textContent = !online ? "接続待ち" : state?.active_job || pendingJob ? "実行中" : "待機中";
    for (const select of document.querySelectorAll(".board-selector")) select.disabled = busy || !boards().length;
  }
  function renderDevices() {
    const groups = boards();
    $("board-count").textContent = `${groups.length} 台`;
    const signature = JSON.stringify([state?.devices, state?.discovery_error]);
    if (signature === devicesSignature) return;
    devicesSignature = signature;
    const container = $("devices-list");
    container.replaceChildren();
    if (!groups.length) {
      const empty = el("div", "card empty-state");
      empty.append(el("span", "empty-symbol", "◉"), el("h3", "", "FlatSat が見つかりません"),
        el("p", "", state?.discovery_error || "USB ケーブルとボードの接続を確認して、再確認してください。"));
      container.append(empty);
    }
    for (const board of groups) {
      const card = el("article", "card device-card"), heading = el("div", "device-heading");
      const name = el("div", "device-name"), labels = el("div");
      labels.append(el("h3", "", "FlatSat v1.0 RP2040"), el("p", "device-serial", `SERIAL  ${board.serial || board.id}`));
      name.append(el("span", "device-icon", "▣"), labels);
      heading.append(name, badge(shellReady(board) ? "センサー取得可能" : "ポートを確認", shellReady(board) ? "ready" : "wait"));
      const ports = el("div", "ports-grid");
      for (const port of board.ports) {
        const item = el("div", "port-card");
        const roleName = {shell: "Cat-Shell", radio0: "Cat-Radio0", radio1: "Cat-Radio1", unknown: "役割不明"}[port.role] || "役割不明";
        item.append(el("span", "port-role", roleName), badge(port.role === "unknown" ? "確認が必要" : port.accessible ? "利用可能" : "権限なし", port.role === "unknown" || !port.accessible ? "wait" : "ready"), el("span", "port-path", port.path));
        ports.append(item);
      }
      card.append(heading, ports);
      if (board.ports.some((port) => !port.accessible)) card.append(el("p", "port-notice", "「権限なし」のポートは、このユーザーで読み書きできません。シリアルポートのアクセス権を設定してください。"));
      if (board.ports.some((port) => port.role === "unknown")) card.append(el("p", "port-notice", "役割を確認できないポートは、操作対象に含めません。"));
      container.append(card);
    }
    for (const select of document.querySelectorAll(".board-selector")) {
      const previous = select.value;
      select.replaceChildren();
      if (!groups.length) select.append(new Option("接続ボードがありません", ""));
      else {
        if (groups.length > 1) select.append(new Option("ボードを選択してください", ""));
        for (const board of groups) select.append(new Option(board.serial || board.id, board.id));
        if (groups.some((board) => board.id === previous)) select.value = previous;
      }
    }
  }

  function jobContent(job) {
    const content = el("div");
    content.append(el("p", "job-title", tasks[job.task] || job.task));
    const meta = el("div", "job-meta");
    meta.append(el("span", "", `開始 ${time(job.started_at)}`), el("span", "", `${number(job.elapsed_s, 1)} 秒経過`));
    content.append(meta);
    if (job.status === "running") {
      const progress = el("div", "progress-track"), bar = el("div", "progress-bar");
      const percent = finite(job.duration_s) && job.duration_s > 0 ? Math.min(98, Math.max(0, job.elapsed_s / job.duration_s * 100)) : 30;
      bar.style.width = `${percent}%`;
      progress.append(bar); content.append(progress);
      if (finite(job.duration_s)) content.append(el("p", "caption", `設定した記録時間 ${number(job.duration_s, 1)} 秒 · 応答待ちで延びる場合があります`));
    }
    if (job.task === "watch") {
      const counters = el("div", "job-counters");
      const count = el("div", "job-counter");
      count.append(el("span", "", "記録したサンプル"), el("strong", "", number(job.progress?.sample_count, 0)));
      const valid = el("div", "job-counter");
      valid.append(el("span", "", job.status === "running" ? "最後のサンプル" : "有効なサンプル"),
        el("strong", "", job.status === "running" ? ({ok: "取得済み", partial: "一部欠測", timeout: "応答なし", invalid: "不正な応答"}[job.progress?.last_sample?.status] || "待機中") : number(job.progress?.valid_count, 0)));
      counters.append(count, valid); content.append(counters);
      const last = job.progress?.last_sample;
      if (last?.status === "ok") {
        const values = el("div", "last-sample");
        for (const metric of ["temperature_c", "pressure_pa", "humidity_percent"]) {
          const item = el("div");
          item.append(el("span", "", metrics[metric].label), el("strong", "", `${number(last.values?.[metric])} ${metrics[metric].unit}`)); values.append(item);
        }
        content.append(values);
      }
    }
    if (job.error) content.append(el("p", "form-error", job.error));
    if (job.status !== "running" && job.capture_id) {
      const link = el("button", "text-button", "保存ログを見る →");
      link.type = "button";
      link.addEventListener("click", () => { switchView("captures"); selectCapture(job.capture_id); });
      content.append(link);
    }
    return content;
  }
  function renderJobs() {
    const job = state?.active_job, recent = state?.recent_jobs || [];
    $("job-badge").className = `badge badge-${job ? "ready" : "neutral"}`;
    $("job-badge").textContent = job ? "実行中" : "待機中";
    if (job) $("active-job").replaceChildren(jobContent(job));
    else {
      const content = el("div", "job-empty");
      content.append(el("p", "muted", "実行中の操作はありません。"), el("p", "", "計測画面からセンサーの連続記録を始められます。"));
      const button = el("button", "text-button", "計測画面へ →"); button.type = "button";
      button.addEventListener("click", () => switchView("measurement")); content.append(button);
      $("active-job").replaceChildren(content);
    }
    const measurement = job || recent.find((item) => item.task === "watch" || item.task === "monitor");
    if (measurement) $("measurement-job").replaceChildren(jobContent(measurement));
    else $("measurement-job").replaceChildren(el("p", "empty-inline", "開始すると進行状況を表示します。"));
    const history = $("job-history");
    if (!recent.length) history.replaceChildren(el("p", "empty-inline", "まだ操作履歴はありません。"));
    else {
      const table = el("table"), head = el("thead"), row = el("tr"), body = el("tbody");
      for (const title of ["操作", "開始", "結果", "記録"]) row.append(el("th", "", title));
      head.append(row);
      for (const item of recent.slice(0, 8)) {
        const record = el("tr");
        record.append(el("td", "", tasks[item.task] || item.task), el("td", "", time(item.started_at)));
        const status = el("td"); status.append(badge(statuses[item.status] || item.status, item.status === "failed" ? "failed" : item.status === "running" ? "ready" : "neutral")); record.append(status);
        const action = el("td");
        if (item.status !== "running" && item.capture_id && state.captures.some((capture) => capture.id === item.capture_id)) {
          const button = el("button", "text-button", "ログを見る"); button.type = "button";
          button.addEventListener("click", () => { switchView("captures"); selectCapture(item.capture_id); }); action.append(button);
        } else action.textContent = "—";
        record.append(action); body.append(record);
        const previous = knownJobs.get(item.id);
        if (previous === "running" && item.status !== "running") {
          toast(item.status === "completed" ? `${tasks[item.task]}が完了しました。` : `${tasks[item.task]}に失敗しました。${item.error || ""}`, item.status === "failed");
          if (item.id === ownJob && item.capture_id && state.captures.some((capture) => capture.id === item.capture_id)) {
            if (item.task === "info") loadInfo(item.capture_id);
            else if (view === "measurement") { switchView("captures"); selectCapture(item.capture_id); }
          }
        }
        knownJobs.set(item.id, item.status);
      }
      table.append(head, body); const wrap = el("div", "table-wrap"); wrap.append(table); history.replaceChildren(wrap);
    }
  }

  function renderCaptureList() {
    const captures = state?.captures || [];
    $("capture-count").textContent = `${captures.length} 件`;
    const signature = JSON.stringify([captures, selectedCapture]);
    if (signature === capturesSignature) return;
    capturesSignature = signature;
    const picker = $("capture-picker");
    if (picker) {
      const placeholder = new Option(captures.length ? "ログを選択してください" : "保存ログがありません", "");
      placeholder.disabled = captures.length > 0;
      picker.replaceChildren(placeholder);
      for (const capture of captures) picker.append(new Option(`${capture.label || modes[capture.mode] || "名称未設定"} · ${time(capture.time_utc)} · #${capture.id.slice(0, 6)}`, capture.id));
      picker.value = captures.some((capture) => capture.id === selectedCapture) ? selectedCapture : "";
      picker.disabled = !captures.length;
    }
    const list = $("capture-list"); list.replaceChildren();
    if (!captures.length) list.append(el("div", "empty-inline", "保存ログがありません。計測を開始するか、過去の JSONL を取り込んでください。"));
    for (const capture of captures) {
      const item = el("button", `capture-item${capture.id === selectedCapture ? " active" : ""}`);
      item.type = "button"; item.setAttribute("aria-pressed", String(capture.id === selectedCapture));
      item.append(el("span", "capture-item-title", capture.label || modes[capture.mode] || "名称未設定"), el("span", "capture-item-time", time(capture.time_utc)));
      const bottom = el("span", "capture-item-bottom");
      bottom.append(badge(modes[capture.mode] || capture.mode || "ログ"), el("span", "", capture.mode === "watch" ? `${number(capture.valid_count, 0)} / ${number(capture.sample_count, 0)} 件有効` : capture.completion === "complete" ? "記録終了" : "終了状態を確認"));
      item.append(bottom); item.addEventListener("click", () => selectCapture(capture.id)); list.append(item);
    }
  }
  async function selectCapture(id) {
    selectionRestored = true;
    selectedCapture = id; renderCaptureList();
    saveSelection();
    const request = ++captureRequest;
    $("capture-detail").replaceChildren(el("div", "empty-state", "ログを読み込んでいます…"));
    try {
      const data = await api(`/api/captures/${encodeURIComponent(id)}`);
      if (request !== captureRequest) return;
      loadedCapture = data; renderCapture(data);
    } catch (error) {
      if (request !== captureRequest) return;
      loadedCapture = null;
      $("capture-detail").replaceChildren(el("div", "form-error", error.message));
    }
  }
  function safeCaptureLink(id, type, label) {
    const link = el("a", "button button-secondary", label);
    link.href = type === "report" ? `/reports/${encodeURIComponent(id)}` : `/api/captures/${encodeURIComponent(id)}/raw`;
    if (type === "report") { link.target = "_blank"; link.rel = "noopener"; }
    else link.download = `${id}.jsonl`;
    return link;
  }
  function renderQueries(queries, container) {
    for (const query of queries || []) {
      const item = el("details", "query-result"), title = el("summary");
      title.append(el("span", "mono", query.command || "応答"), badge(query.answered ? "応答あり" : "応答未確認", query.answered ? "ready" : "wait"));
      item.append(title, el("pre", "", typeof query.text === "string" ? query.text : "応答データがありません。")); container.append(item);
    }
  }
  async function loadInfo(id) {
    try {
      const data = await api(`/api/captures/${encodeURIComponent(id)}`);
      $("info-result").replaceChildren(); renderQueries(data.queries, $("info-result")); $("info-result").hidden = false;
    } catch (error) { toast(error.message, true); }
  }
  function summaryStat(label, value, unit) {
    const stat = el("div", "summary-stat"), strong = el("strong", "", value);
    if (unit) strong.append(el("small", "", unit)); stat.append(el("span", "", label), strong); return stat;
  }
  function renderCapture(data) {
    const container = $("capture-detail"); container.replaceChildren();
    const session = data.session || {}, summary = data.summary || {}, quality = summary.quality || {};
    const heading = el("div", "detail-heading"), name = el("div");
    name.append(el("p", "eyebrow", "EXPERIMENT LOG"), el("h2", "", session.label || modes[session.mode] || "名称未設定"));
    heading.append(name, badge(quality.completion === "complete" ? "記録終了" : quality.completion === "interrupted" ? "中断" : "終了未確認", quality.completion === "complete" ? "ready" : "wait")); container.append(heading);
    const meta = el("div", "detail-meta"); meta.append(el("span", "", time(session.time_utc, true)), el("span", "", modes[session.mode] || session.mode || "ログ"));
    const boardIds = [...new Set((session.ports || []).map((port) => port.serial_number || port.board_id).filter(Boolean))];
    if (boardIds.length) meta.append(el("span", "mono", `SERIAL ${boardIds.join(", ")}`)); container.append(meta);
    if (session.notes?.length) container.append(el("p", "detail-note", session.notes.join("\n")));
    const actions = el("div", "detail-actions"); actions.append(safeCaptureLink(data.id, "report", "レポートを開く ↗"), safeCaptureLink(data.id, "raw", "JSONL を保存 ↓")); container.append(actions);
    if (quality.completion !== "complete") container.append(el("p", "detail-warning", "このログの終了状態は中断または未確認です。有効なサンプルだけを統計に使っています。"));
    if (session.mode === "monitor") {
      const received = el("div", "summary-grid"); received.append(summaryStat("受信データ", number(data.received_bytes, 0), "bytes")); container.append(received);
      container.append(el("p", "muted", data.received_bytes > 0 ? "受信した生データを JSONL から確認できます。センサー計測としての統計はありません。" : "この記録には受信データがありません。記録中にデータが届かなかったことを示します。"));
    } else if (Array.isArray(data.samples) && data.samples.length) {
      const stats = el("div", "summary-grid");
      stats.append(summaryStat("サンプル", number(summary.sample_count, 0), "件"), summaryStat("有効", number(summary.valid_count, 0), "件"), summaryStat("欠測・不正", number(summary.failed_count, 0), "件"), summaryStat("取得頻度（ホスト）", number(summary.timing?.effective_hz, 1), "Hz")); container.append(stats);
      const graphHeading = el("div", "chart-heading"), select = el("select"); select.id = "capture-metric"; select.setAttribute("aria-label", "グラフに表示するセンサー");
      for (const [key, metric] of Object.entries(metrics)) select.append(new Option(metric.label, key));
      if (!Object.hasOwn(metrics, currentMetric)) currentMetric = Object.keys(metrics)[0]; select.value = currentMetric;
      graphHeading.append(el("h3", "", "計測値の推移"), select); container.append(graphHeading, el("div", "chart-box"));
      container.lastChild.id = "capture-chart";
      const caption = el("p", "chart-caption"); caption.id = "chart-caption"; container.append(caption);
      const statistics = el("div", "stats-table table-wrap"); statistics.id = "metric-statistics"; container.append(statistics);
      select.addEventListener("change", () => { currentMetric = select.value; saveSelection(); renderMetric(data); }); renderMetric(data);
    } else if (session.mode === "watch") container.append(el("div", "empty-inline", "センサーのサンプルは記録されていません。"));
    if (data.alerts) renderAlerts(data.alerts, container);
    if (data.queries?.length) {
      const queries = el("div", "queries-section"); queries.append(el("h3", "", "ボードからの応答")); renderQueries(data.queries, queries); container.append(queries);
    }
  }

  function svgEl(tag, attributes, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function renderMetric(data) {
    const metric = metrics[currentMetric], samples = data.samples || [];
    const chart = $("capture-chart"); chart.replaceChildren();
    const points = samples.map((sample, index) => ({value: sample.status === "ok" && finite(sample.values?.[currentMetric]) ? sample.values[currentMetric] : null, elapsed: sample.elapsed_s, index}));
    const valid = points.filter((point) => point.value !== null);
    const timed = points.length > 1 && points.every((point, index) => finite(point.elapsed) && (index === 0 || point.elapsed > points[index - 1].elapsed));
    $("chart-caption").textContent = `${timed ? "横軸：ホストの問い合わせ開始からの経過秒。" : "横軸：記録順。時間情報が不足するか、順序を確認できません。"} 欠測・不正なサンプルの間は線をつなぎません。加速度には重力が含まれます。`;
    if (!valid.length) chart.append(el("div", "empty-state", "この項目の有効な計測値がありません。"));
    else {
      const style = getComputedStyle(chart);
      const contentWidth = chart.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const width = Math.max(220, Math.round(contentWidth > 0 ? contentWidth : 600));
      const height = width < 420 ? 230 : 240, left = 68, right = 12, top = 26, bottom = 36;
      const scale = valid.reduce((largest, point) => Math.max(largest, Math.abs(point.value)), 1);
      let low = valid.reduce((smallest, point) => Math.min(smallest, point.value / scale), Infinity);
      let high = valid.reduce((largest, point) => Math.max(largest, point.value / scale), -Infinity);
      const pad = high > low ? (high - low) * .12 : Math.max(Math.abs(low) * .02, .0001);
      low -= pad; high += pad;
      const xValues = points.map((point) => timed ? point.elapsed : point.index);
      const xScale = xValues.reduce((largest, value) => Math.max(largest, Math.abs(value)), 1);
      const first = xValues[0] / xScale, last = xValues[xValues.length - 1] / xScale;
      const x = (point) => left + ((timed ? point.elapsed : point.index) / xScale - first) / (last - first || 1) * (width - left - right);
      const y = (value) => top + (high - value / scale) / (high - low) * (height - top - bottom);
      const svg = svgEl("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${metric.label}のグラフ。有効な値 ${valid.length} 件。`});
      svg.append(svgEl("title", {}, `${metric.label}（${metric.unit}）`));
      for (let index = 0; index < 4; index++) {
        const relative = index / 3, yy = top + relative * (height - top - bottom), value = (high - relative * (high - low)) * scale;
        svg.append(svgEl("line", {x1: left, x2: width - right, y1: yy, y2: yy, stroke: "#e3edf2", "stroke-width": 1}));
        svg.append(svgEl("text", {x: left - 9, y: yy + 4, "text-anchor": "end", fill: "#718695", "font-size": 11}, number(value, 2)));
      }
      const limit = data.alerts?.limits?.find((item) => item.metric === currentMetric);
      for (const bound of [limit?.minimum, limit?.maximum]) {
        if (!finite(bound) || bound / scale < low || bound / scale > high) continue;
        svg.append(svgEl("line", {x1: left, x2: width - right, y1: y(bound), y2: y(bound), stroke: "#a07c36", "stroke-width": 1, "stroke-dasharray": "5 4"}));
      }
      let segment = [];
      function drawSegment() {
        if (!segment.length) return;
        if (segment.length > 1) svg.append(svgEl("path", {d: segment.map((point, index) => `${index ? "L" : "M"}${x(point).toFixed(2)} ${y(point.value).toFixed(2)}`).join(" "), fill: "none", stroke: "#07879d", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
        else svg.append(svgEl("circle", {cx: x(segment[0]), cy: y(segment[0].value), r: 3, fill: "#07879d"}));
        segment = [];
      }
      for (const point of points) { if (point.value === null) drawSegment(); else segment.push(point); } drawSegment();
      svg.append(svgEl("text", {x: 7, y: 14, fill: "#718695", "font-size": 11}, metric.unit));
      for (let index = 0; index < 3; index++) {
        const fraction = index / 2, xx = left + fraction * (width - left - right);
        const value = timed ? xValues[0] * (1 - fraction) + xValues[xValues.length - 1] * fraction : Math.round(fraction * (points.length - 1)) + 1;
        svg.append(svgEl("text", {x: xx, y: height - 15, "text-anchor": index === 0 ? "start" : index === 2 ? "end" : "middle", fill: "#718695", "font-size": 11}, `${number(value, timed ? 1 : 0)}${timed ? " s" : " 件目"}`));
      }
      chart.append(svg);
    }
    const stats = data.summary?.metrics?.[currentMetric] || {}, table = el("table"), head = el("tr"), row = el("tr");
    for (const [key, label] of [["mean", "平均"], ["min", "最小"], ["max", "最大"], ["stdev", "標準偏差"], ["delta", "変化量"]]) {
      head.append(el("th", "", label)); row.append(el("td", "", `${number(stats[key])}${finite(stats[key]) ? " " + metric.unit : ""}`));
    }
    const thead = el("thead"), tbody = el("tbody"); thead.append(head); tbody.append(row); table.append(thead, tbody); $("metric-statistics").replaceChildren(table);
  }
  function renderAlerts(alerts, container) {
    const section = el("div", "events-section"); section.append(el("h3", "", "しきい値とイベント"));
    const limits = el("div", "limits-list");
    for (const limit of alerts.limits || []) {
      const metric = metrics[limit.metric] || {label: limit.metric, unit: ""};
      const bounds = `${limit.minimum === null ? "下限なし" : "下限 " + String(limit.minimum)} / ${limit.maximum === null ? "上限なし" : "上限 " + String(limit.maximum)}`;
      limits.append(el("span", "limit-chip", `${metric.label} · ${bounds} ${metric.unit}`));
    }
    section.append(limits);
    const overview = el("div", "event-summary");
    overview.append(el("span", "", `範囲外 ${number(alerts.trigger_count, 0)} 件`), el("span", "", `回復 ${number(alerts.recovery_count, 0)} 件`), el("span", "", `欠測 ${number(alerts.unavailable_count, 0)} 件`)); section.append(overview);
    if (!alerts.events?.length) section.append(el("p", "muted", "設定したしきい値での状態変化はありません。"));
    else {
      const table = el("table"), header = el("tr"), head = el("thead"), body = el("tbody");
      for (const label of ["経過", "項目", "イベント", "値"]) header.append(el("th", "", label)); head.append(header);
      for (const event of alerts.events.slice(0, 100)) {
        const row = el("tr"), metric = metrics[event.metric] || {label: event.metric, unit: ""};
        row.append(el("td", "", finite(event.elapsed_s) ? `${number(event.elapsed_s, 2)} s` : time(event.time_utc)), el("td", "", metric.label));
        const transition = el("td"); transition.append(badge(transitions[event.transition] || event.transition, event.transition === "triggered" || event.transition === "unavailable" ? "wait" : "ready"));
        row.append(transition, el("td", "", finite(event.value) ? `${String(event.value)} ${metric.unit}` : "—")); body.append(row);
      }
      table.append(head, body); const wrap = el("div", "table-wrap"); wrap.append(table); section.append(wrap);
      if (alerts.events.length > 100) section.append(el("p", "caption", "先頭 100 件を表示しています。全件はレポートで確認できます。"));
    }
    container.append(section);
  }

  function addLimit() {
    const rows = $("limit-fields").children;
    if (rows.length >= Object.keys(metrics).length) { toast("すべての項目にしきい値を設定できます。同じ項目を重複させないでください。"); return; }
    const row = el("div", "limit-row"), select = el("select"), minimum = el("input"), maximum = el("input");
    const used = [...rows].map((item) => item.querySelector("select").value);
    for (const [key, metric] of Object.entries(metrics)) select.append(new Option(`${metric.label} (${metric.unit})`, key));
    select.value = Object.keys(metrics).find((key) => !used.includes(key)) || Object.keys(metrics)[0];
    select.setAttribute("aria-label", "しきい値を設定する項目");
    for (const [input, label] of [[minimum, "下限"], [maximum, "上限"]]) {
      input.type = "text"; input.inputMode = "text"; input.placeholder = "未設定";
      input.pattern = "(?:\\+|-)?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE](?:\\+|-)?[0-9]+)?";
      input.title = "数値を入力してください。負の値や小数も指定できます。";
      input.setAttribute("aria-label", label);
    }
    for (const [input, label] of [[select, "項目"], [minimum, "下限"], [maximum, "上限"]]) { const field = el("div"), caption = el("label", "", label); caption.append(input); field.append(caption); row.append(field); }
    const remove = el("button", "remove-limit", "×"); remove.type = "button"; remove.setAttribute("aria-label", "このしきい値を削除"); remove.addEventListener("click", () => row.remove()); row.append(remove);
    $("limit-fields").append(row); select.focus();
  }
  function configuredLimits() {
    const used = new Set(), limits = [];
    for (const row of $("limit-fields").children) {
      const metric = row.querySelector("select").value, inputs = row.querySelectorAll("input");
      const lower = inputs[0].value.trim(), upper = inputs[1].value.trim();
      if (!lower && !upper) throw new Error("しきい値の下限または上限を入力してください。");
      if (used.has(metric)) throw new Error("同じ項目のしきい値は一つにまとめてください。");
      if ((lower && !Number.isFinite(Number(lower))) || (upper && !Number.isFinite(Number(upper)))) throw new Error("しきい値には有限の数値を入力してください。");
      if (lower && upper && Number(lower) > Number(upper)) throw new Error("しきい値の下限は上限以下にしてください。");
      used.add(metric); limits.push(`${metric}:${lower}:${upper}`);
    }
    return limits;
  }
  async function startJob(body, errorId) {
    if (pending || state?.active_job) return;
    if (errorId) showError(errorId, "");
    pending = true; pendingJob = true; updateActions();
    try {
      const result = await api("/api/jobs", body);
      ownJob = result.job.id; knownJobs.set(result.job.id, "running");
      state.active_job = result.job; state.recent_jobs = [result.job, ...state.recent_jobs.filter((item) => item.id !== result.job.id)];
      renderJobs(); toast(`${tasks[body.task]}を開始しました。`);
    } catch (error) { if (errorId) showError(errorId, error.message); else toast(error.message, true); }
    finally { pending = false; pendingJob = false; updateActions(); }
  }
  async function importCapture(file) {
    showError("import-error", "");
    if (!file) return;
    if (file.size > 2 * 1024 * 1024 - 1024) { showError("import-error", "取り込めるファイルは 2 MiB 未満です。"); $("capture-import").value = ""; return; }
    pending = true; updateActions();
    try {
      const result = await api("/api/captures/import", {name: file.name, content: await file.text()});
      state.captures = [result.capture, ...state.captures]; renderCaptureList(); selectCapture(result.capture.id);
      toast("ログを取り込みました。");
    } catch (error) { showError("import-error", error.message); }
    finally { pending = false; $("capture-import").value = ""; updateActions(); }
  }

  async function loadExercises() {
    try {
      const result = await api("/api/exercises"), exercises = Array.isArray(result) ? result : result.exercises || [];
      const container = $("exercises-list"); container.replaceChildren();
      if (!exercises.length) container.append(el("div", "card empty-inline", "演習一覧がありません。"));
      for (const exercise of exercises) {
        const card = el("article", "card exercise-card"), heading = el("div", "exercise-heading");
        heading.append(el("span", "exercise-id", exercise.id), badge(exercise.layer || "演習"));
        card.append(heading, el("h3", "", exercise.title || exercise.title_en || exercise.id), el("p", "muted", exercise.description));
        const meta = el("div", "exercise-meta");
        for (const detail of [exercise.difficulty, exercise.duration, exercise.prerequisite ? `前提：${exercise.prerequisite}` : null]) if (detail) meta.append(el("span", "", detail)); card.append(meta);
        if (exercise.command) {
          const command = el("div", "exercise-command"), copy = el("button", "copy-button", "コピー"); copy.type = "button";
          copy.addEventListener("click", () => copyCommand(exercise.command));
          command.append(el("code", "", exercise.command), copy); card.append(command);
        }
        if (/^EX-[A-Z]\d{2}$/.test(exercise.id || "")) {
          const link = el("a", "text-button", "演習の説明を読む ↗"); link.href = `/api/exercises/${encodeURIComponent(exercise.id)}/readme?lang=ja`; link.target = "_blank"; link.rel = "noopener"; card.append(link);
        }
        container.append(card);
      }
      exercisesLoaded = true;
    } catch (error) { $("exercises-list").replaceChildren(el("div", "card form-error", error.message)); }
  }

  async function copyCommand(command) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(command); toast("実行コマンドをコピーしました。"); return;
      }
    } catch (_) { /* HTTP and clipboard-denied browsers use a selectable command. */ }
    document.querySelector(".command-copy-dialog")?.remove();
    const dialog = el("dialog", "command-copy-dialog"), title = el("h2", "", "コマンドをコピー");
    title.id = "command-copy-title"; dialog.setAttribute("aria-labelledby", title.id);
    const description = el("p", "muted", "選択されたコマンドを長押ししてコピーしてください。");
    const text = el("textarea"); text.readOnly = true; text.rows = 4; text.spellcheck = false;
    text.value = command; text.setAttribute("aria-label", "実行コマンド");
    const actions = el("div", "command-copy-actions"), close = el("button", "button button-secondary", "閉じる");
    close.type = "button";
    close.addEventListener("click", () => { if (typeof dialog.close === "function") dialog.close(); else dialog.remove(); });
    actions.append(close); dialog.append(title, description, text, actions);
    dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog);
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
    text.focus({preventScroll: true}); text.select();
  }

  async function refresh() {
    if (controller || stopped) return;
    clearTimeout(timer); controller = new AbortController(); const abort = setTimeout(() => controller?.abort(), 10000);
    try {
      const response = await fetch("/api/state", {cache: "no-store", credentials: "same-origin", signal: controller.signal});
      if (!response.ok) throw new Error(`状態を読み込めませんでした（${response.status}）。`);
      state = await response.json(); metrics = state.metrics || metricDefaults; online = true;
      $("server-dot").className = "status-dot online"; $("server-status").textContent = "接続中";
      $("last-updated").textContent = new Date().toLocaleTimeString("ja-JP", {hour: "2-digit", minute: "2-digit", second: "2-digit"});
      showError("connection-error", state.discovery_error ? `デバイス確認：${state.discovery_error}` : "");
      renderDevices(); renderCaptureList(); restoreSelection(); renderJobs();
    } catch (error) {
      online = false; $("server-dot").className = "status-dot offline"; $("server-status").textContent = "接続待ち";
      showError("connection-error", "コンソールに接続できません。サーバーの起動状態を確認してください。自動的に再接続します。");
    } finally {
      clearTimeout(abort); controller = null; updateActions();
      if (!stopped) timer = setTimeout(refresh, online ? 1000 : 3000);
    }
  }

  for (const button of document.querySelectorAll("[data-view]")) button.addEventListener("click", () => switchView(button.dataset.view));
  for (const select of document.querySelectorAll(".board-selector")) select.addEventListener("change", updateActions);
  $("monitor-port").addEventListener("change", updateActions);
  $("refresh-devices").addEventListener("click", refresh);
  $("add-limit").addEventListener("click", addLimit);
  $("capture-import").addEventListener("change", (event) => importCapture(event.target.files[0]));
  $("capture-picker")?.addEventListener("change", (event) => { if (event.target.value) selectCapture(event.target.value); });
  $("info-form").addEventListener("submit", (event) => { event.preventDefault(); startJob({task: "info", serial: $("info-board").value}); });
  $("watch-form").addEventListener("submit", (event) => {
    event.preventDefault(); showError("watch-error", "");
    try { startJob({task: "watch", serial: $("watch-board").value, duration: Number($("watch-duration").value), interval: Number($("watch-interval").value), label: $("watch-label").value.trim(), notes: $("watch-note").value.trim() ? [$("watch-note").value.trim()] : [], limits: configuredLimits()}, "watch-error"); }
    catch (error) { showError("watch-error", error.message); }
  });
  $("monitor-form").addEventListener("submit", (event) => { event.preventDefault(); startJob({task: "monitor", serial: $("monitor-board").value, port: $("monitor-port").value, duration: Number($("monitor-duration").value)}, "monitor-error"); });
  window.addEventListener("hashchange", () => switchView(location.hash.slice(1), false));
  window.addEventListener("resize", () => {
    cancelAnimationFrame(chartResizeFrame);
    chartResizeFrame = requestAnimationFrame(() => {
      if (view === "captures" && loadedCapture?.samples?.length && $("capture-chart")) renderMetric(loadedCapture);
    });
  });
  window.addEventListener("pagehide", () => { stopped = true; clearTimeout(timer); clearTimeout(toastTimer); controller?.abort(); });
  window.addEventListener("pageshow", () => { stopped = false; refresh(); });
  switchView(location.hash.slice(1) || "connection", false); updateActions(); refresh();
})();
