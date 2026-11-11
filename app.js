const byId = (id) => document.getElementById(id);
const form = byId("brief-form");
const labels = {
  review: "Ready for review",
  approved: "Approved",
  sending: "Checking delivery",
  needs_check: "Check needed",
  draft_created: "Draft confirmed",
};
const componentLabels = {hero: "Hero", benefits: "Benefits", cta: "Call to action"};
let config;
let run;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = typeof value.detail === "string" ? value.detail : value.detail?.[0]?.msg;
    throw new Error(detail || "The request could not complete.");
  }
  return value;
}

async function busy(work, button, message) {
  const oldText = button?.textContent;
  byId("error").hidden = true;
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((item) => { item.disabled = true; });
  byId("recent").disabled = true;
  if (button && message) button.textContent = message;
  try {
    await work();
  } catch (error) {
    byId("error").textContent = error.message;
    byId("error").hidden = false;
  } finally {
    if (button) button.textContent = oldText;
    buttons.forEach((item) => { item.disabled = false; });
    byId("recent").disabled = false;
  }
}

function loadExample() {
  for (const [key, value] of Object.entries(config.example)) {
    form.elements[key].value = Array.isArray(value) ? value.join("\n") : value;
  }
  form.elements.file_key.value = "";
  form.elements.node_id.value = "";
  byId("design-source").textContent = "Sample Figma frame";
}

function render(value) {
  run = value;
  const review = byId("review");
  review.classList.remove("empty");
  review.replaceChildren(element("h3", run.plan.title, "page-title"));
  for (const section of run.plan.sections) {
    const block = element("section", undefined, "preview-section");
    block.append(
      element("div", componentLabels[section.component], "component-label"),
      element("h3", section.heading),
      element("p", section.body),
    );
    if (section.points.length) {
      const list = element("ul");
      for (const point of section.points) list.append(element("li", point));
      block.append(list);
    }
    if (section.component === "cta") {
      const button = element("span", run.brief.action, "preview-button");
      button.title = run.brief.action_path;
      block.append(button);
    }
    review.append(block);
  }
  byId("review-subtitle").textContent = run.design.name + " · " + run.planner;
  byId("status").hidden = false;
  byId("status").dataset.state = run.state;
  byId("status").textContent = labels[run.state];
  byId("actions").hidden = false;
  byId("revision").textContent = "Revision " + run.revision.slice(0, 12) + " · " + run.destination;
  byId("markup").href = "/api/runs/" + run.id + "/blocks";
  byId("approve").hidden = run.state !== "review";
  byId("deliver").hidden = run.state !== "approved";
  byId("reconcile").hidden = !["sending", "needs_check"].includes(run.state);
  byId("simulate-row").hidden = run.state !== "approved" || !config.sample_delivery;
  byId("draft-link").hidden = !run.draft;
  if (run.draft) byId("draft-link").href = run.draft.url;

  const index = run.state === "review" ? 1 : run.state === "approved" ? 2 : 3;
  [...document.querySelectorAll(".steps li")].forEach((item, position) => {
    item.classList.toggle("done", position < index || run.state === "draft_created");
    if (position === index) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });

  byId("activity-card").hidden = false;
  const activity = byId("activity-list");
  activity.replaceChildren();
  for (const event of run.events) {
    const entry = element("li");
    const time = element("time", new Date(event.at).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}));
    time.dateTime = event.at;
    entry.append(element("span", event.detail), time);
    activity.append(entry);
  }
  const url = new URL(location.href);
  url.searchParams.set("run", run.id);
  history.replaceState(null, "", url);
}

async function refreshRuns() {
  const records = await request("/api/runs");
  const select = byId("recent");
  select.replaceChildren(new Option("Choose a run", ""));
  for (const record of records) {
    select.append(new Option(record.title + " · " + labels[record.state], record.id));
  }
  if (run) select.value = run.id;
}

byId("reset").addEventListener("click", () => { if (config) loadExample(); });
byId("recent").addEventListener("change", () => {
  const id = byId("recent").value;
  if (id) busy(async () => render(await request("/api/runs/" + id)));
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!config) return;
  busy(async () => {
    const fields = new FormData(form);
    const brief = Object.fromEntries(["campaign", "audience", "goal", "action", "action_path"].map((key) => [key, fields.get(key)]));
    brief.points = fields.get("points").split("\n").map((line) => line.trim()).filter(Boolean);
    const file_key = fields.get("file_key").trim();
    const node_id = fields.get("node_id").trim();
    const design = file_key || node_id ? {file_key, node_id} : null;
    byId("simulate").checked = false;
    render(await request("/api/runs", {brief, design}));
    byId("design-source").textContent = run.design.source;
    await refreshRuns();
  }, byId("generate"), "Generating review…");
});

const actions = [
  ["approve", () => ({revision: run.revision}), "Approving…"],
  ["deliver", () => ({simulate_lost_reply: byId("simulate").checked}), "Creating draft…"],
  ["reconcile", () => ({}), "Looking up draft…"],
];
for (const [name, body, progress] of actions) {
  const button = byId(name);
  button.addEventListener("click", () => busy(async () => {
    render(await request("/api/runs/" + run.id + "/" + name, body()));
    await refreshRuns();
  }, button, progress));
}

busy(async () => {
  config = await request("/api/settings");
  byId("environment").textContent = config.planner + " · " + config.destination;
  byId("figma-settings").hidden = !config.supports_figma;
  loadExample();
  const id = new URL(location.href).searchParams.get("run");
  if (id) render(await request("/api/runs/" + encodeURIComponent(id)));
  await refreshRuns();
});
