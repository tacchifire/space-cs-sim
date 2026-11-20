"use strict";
// Design 4.4 names the version field but does not pin its value.
const PROTOCOL_VERSION = 1;
const SENSOR_SET_BLOCKLIST = new Set(["magnetic-flux-density"]); // D3: crashes Renode.
const ACTIONS = ["status", "command", "exec-renode", "exec-monitor", "term-resize", "spawn", "kill",
  "fs/list", "fs/mkdir", "fs/stat", "fs/dwnl"];
const RENODE_ACTIONS = ["machines", "uarts", "buttons", "leds", "button-set", "sensors", "sensor-get", "sensor-set"];
const EVENTS = ["uart-opened", "led-state-changed", "button-state-changed", "renode-quitted"];
const $ = id => document.getElementById(id);
let socket = null, sequence = 0, terminalBase = "", connected = false;
const pending = new Map(), terminals = new Map(), peripheralState = new Map();
const pretty = value => JSON.stringify(value, null, 2);
function log(value) {
  const area = $("event-log");
  if (area.textContent === "not connected") area.textContent = "";
  area.textContent += `${new Date().toLocaleTimeString()} ${typeof value === "string" ? value : pretty(value)}\n`;
  // Bound retained console history.
  area.textContent = area.textContent.slice(-100000);
  area.scrollTop = area.scrollHeight;
}
function state(value) {
  connected = value;
  $("connection").textContent = value ? "connected" : "not connected";
  document.querySelectorAll("[data-connected]").forEach(el => { el.disabled = !value; });
  document.querySelectorAll(".connection-note").forEach(el => { el.textContent = value ? "Live server-mode channel" : "not connected"; });
  $("connect").disabled = value;
  $("disconnect").disabled = !value;
  if (!value) {
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error("not connected")); }
    pending.clear(); terminals.clear(); peripheralState.clear();
    $("machines").replaceChildren(); $("terminal-select").replaceChildren(); $("terminal-links").replaceChildren();
    $("terminal").removeAttribute("src"); $("terminal").hidden = true;
    for (const id of ["peripheral-state", "monitor-output", "event-log"]) $(id).textContent = "not connected";
  }
}
function object(text) {
  const value = JSON.parse(text);
  if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("Payload must be a JSON object keyed by handler argument names");
  return value;
}
function guard(action, payload) {
  // Check recursively so the generic sender cannot bypass D3 through nested arguments.
  const values = value => value && typeof value === "object" ? Object.entries(value).flatMap(([key, item]) => [key, ...values(item)]) : [value];
  const fields = values(payload);
  if (action === "exec-renode" && fields.includes("sensor-set") &&
      fields.some(value => typeof value === "string" && SENSOR_SET_BLOCKLIST.has(value.toLowerCase()))) {
    throw new Error("D3: sensor-set refused for a blocked sensor type");
  }
}
function send(action, payload) {
  return new Promise((resolve, reject) => {
    try {
      if (!connected || socket.readyState !== WebSocket.OPEN) throw new Error("not connected");
      if (!payload || Array.isArray(payload) || typeof payload !== "object") throw new Error("Payload must be an object");
      guard(action, payload);
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Request ${id} timed out`)); }, 15000);
      pending.set(id, {resolve, reject, timer, action, payload});
      socket.send(JSON.stringify({action, payload, version: PROTOCOL_VERSION, id}));
    } catch (error) { reject(error); }
  });
}
function safely(task) { return Promise.resolve().then(task).catch(error => log(`Error: ${error.message}`)); }
function addTerminal(id, label) {
  if (id === undefined || id === null || !connected) return;
  terminals.set(String(id), label || String(id));
  const previous = $("terminal-select").value;
  $("terminal-select").replaceChildren(); $("terminal-links").replaceChildren();
  for (const [key, name] of terminals) {
    $("terminal-select").add(new Option(name, key));
    const link = document.createElement("a");
    link.href = `${terminalBase}/telnet/${encodeURIComponent(key)}`;
    link.textContent = `${name} · open in new tab`; link.target = "_blank"; link.rel = "noopener noreferrer";
    $("terminal-links").append(link);
  }
  if (terminals.has(previous)) $("terminal-select").value = previous;
  showTerminal();
}
function showTerminal() {
  if (!connected || !terminals.size) return;
  $("terminal").src = `${terminalBase}/telnet/${encodeURIComponent($("terminal-select").value)}`;
  $("terminal").hidden = false;
}
function rememberTerminals(data) {
  if (Array.isArray(data)) data.forEach(rememberTerminals);
  else if (data && typeof data === "object") {
    if (data.id !== undefined) addTerminal(data.id, data.name || data.uart || "Monitor / UART " + data.id);
    else Object.values(data).forEach(item => { if (item && typeof item === "object") rememberTerminals(item); });
  }
}
function renderMachines(data) {
  $("machines").replaceChildren();
  const raw = document.createElement("pre"); raw.textContent = pretty(data); $("machines").append(raw);
  const names = Array.isArray(data) ? data : (data && Array.isArray(data.machines) ? data.machines : []);
  for (const item of names) {
    const name = typeof item === "string" ? item : item.name;
    if (typeof name !== "string") continue;
    const box = document.createElement("div"), title = document.createElement("h3");
    title.textContent = name; box.append(title);
    for (const command of ["uarts", "leds", "buttons", "sensors"]) {
      const button = document.createElement("button"); button.textContent = command;
      button.onclick = () => safely(() => send("exec-renode", {command, machine: name})); box.append(button);
    }
    $("machines").append(box);
    for (const command of ["uarts", "leds", "buttons", "sensors"]) {
      safely(() => send("exec-renode", {command, machine: name}));
    }
  }
}
function recordPeripheral(key, data) {
  peripheralState.set(key, data);
  $("peripheral-state").textContent = Array.from(peripheralState, ([name, value]) => `${name}\n${pretty(value)}`).join("\n\n");
}
function receive(message) {
  log(message); // Every response and every push, including unknown events.
  if (Object.hasOwn(message, "event")) {
    if (message.event === "uart-opened") rememberTerminals(message.data);
    if (["led-state-changed", "button-state-changed"].includes(message.event)) recordPeripheral(`${message.event} ${pretty(message.data?.machine)} ${pretty(message.data?.peripheral)}`, message.data);
    if (message.event === "renode-quitted") { socket.close(); state(false); log(message); }
    return;
  }
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id); clearTimeout(request.timer);
  if (message.error || ["error", "failed", false].includes(message.status)) {
    request.reject(new Error(pretty(message.error || message.status))); return;
  }
  if (request.action === "exec-monitor") $("monitor-output").textContent = pretty(message.data);
  if (request.action === "exec-renode") {
    const command = request.payload.command;
    recordPeripheral(`${command} ${request.payload.machine || ""}`, message.data);
    if (command === "machines") renderMachines(message.data);
    if (command === "uarts") rememberTerminals(message.data);
  }
  request.resolve(message.data);
}
$("connect").onclick = () => safely(() => {
  const url = new URL($("ws-url").value);
  if (!["ws:", "wss:"].includes(url.protocol)) throw new Error("Use a ws:// or wss:// URL");
  if (socket) socket.close();
  state(false); $("connection").textContent = "connecting"; $("connect").disabled = true; $("disconnect").disabled = false;
  const current = new WebSocket(url); socket = current;
  terminalBase = `${url.protocol === "wss:" ? "https:" : "http:"}//${url.host}`;
  current.onopen = () => { if (socket !== current) return; state(true); safely(() => send("status", {})); safely(() => send("exec-renode", {command: "machines"})); };
  current.onmessage = event => { if (socket === current) safely(() => receive(JSON.parse(event.data))); };
  current.onerror = () => { if (socket === current) log("WebSocket connection error"); };
  current.onclose = () => { if (socket === current) state(false); };
});
$("disconnect").onclick = () => { if (socket) socket.close(); state(false); };
$("refresh").onclick = () => safely(() => send("exec-renode", {command: "machines"}));
$("monitor-form").onsubmit = event => { event.preventDefault(); safely(() => send("exec-monitor", {command: $("monitor-command").value})); };
$("send").onclick = () => safely(() => send($("action").value, object($("payload").value)));
function preparePeripheral() {
  const payload = {...object($("peripheral-args").value), command: $("operation").value, machine: $("machine").value};
  if ($("peripheral").value) payload.peripheral = $("peripheral").value;
  guard("exec-renode", payload);
  $("action").value = "exec-renode"; $("payload").value = pretty(payload); $("payload").focus();
  return payload;
}
$("prepare-peripheral").onclick = () => safely(preparePeripheral);
$("send-peripheral").onclick = () => safely(() => send("exec-renode", preparePeripheral()));
$("terminal-select").onchange = showTerminal;
$("add-terminal").onclick = () => { if ($("terminal-id").value.trim()) addTerminal($("terminal-id").value.trim()); };
$("exercise-select").onchange = () => safely(async () => {
  const id = $("exercise-select").value; if (!id) return;
  const response = await fetch(`/api/exercises/${encodeURIComponent(id)}`);
  if (!response.ok) throw new Error("Exercise could not be loaded");
  const entry = await response.json();
  const {body, scenario, ...metadata} = entry;
  $("exercise-meta").textContent = pretty(metadata); $("readme").textContent = body;
  $("scenario").value = scenario ? `exercises/${entry.dir}/${scenario}` : "No scenario file";
  $("verify-command").value = `make verify EX=${entry.dir}`;
});
ACTIONS.forEach(action => $("actions").append(new Option(action, action)));
RENODE_ACTIONS.forEach(command => $("operation").add(new Option(command, command)));
state(false);
safely(async () => {
  const [configResponse, listResponse] = await Promise.all([fetch("/api/config"), fetch("/api/exercises")]);
  if (!configResponse.ok || !listResponse.ok) throw new Error("Unable to load GUI configuration or catalog");
  const config = await configResponse.json(); $("ws-url").value = config.renode_ws_url; terminalBase = config.renode_terminal_base;
  for (const entry of await listResponse.json()) $("exercise-select").add(new Option(`${entry.id} · ${entry.title}`, entry.id));
});
