import {
  ERROR,
  MSG,
  WARN,
  buildNodes,
  makeDCTL,
  parseLcp,
} from "./converter.js";

const OUTPUT_NAME = "Vignette Compensation.dctl";

const fileInput = document.getElementById("files");
const dropZone = document.getElementById("drop");
const note = document.getElementById("note");
const list = document.getElementById("list");
const summary = document.getElementById("summary");
const downloadButton = document.getElementById("download");

let records = [];

function addFiles(files) {
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith(".lcp")) {
      records.push({
        file: file.name,
        name: file.name,
        badge: "?",
        warnings: [[ERROR, MSG.notLcp]],
        focals: [],
        nodes: [],
      });
      render();
      continue;
    }
    file.text().then((text) => {
      records.push(parseLcp(text, file.name));
      render();
    });
  }
}

function rowFor(record) {
  const li = document.createElement("li");
  li.className =
    record.nodes.length === 0
      ? "row error"
      : record.warnings.length
        ? "row warn"
        : "row";

  const title = document.createElement("div");
  title.className = "row-title";
  title.textContent = `[${record.badge}] ${record.name}`;
  li.appendChild(title);

  if (record.focals.length) {
    const focals = document.createElement("div");
    focals.className = "focals";
    for (const [index, focal] of record.focals.entries()) {
      const label = document.createElement(focal.usable ? "span" : "s");
      label.textContent = `${Math.round(focal.focal)}mm${focal.usable ? ` f/${focal.aperture.toFixed(1)}` : ""}`;
      focals.appendChild(label);
      if (index < record.focals.length - 1)
        focals.appendChild(document.createTextNode(" \u2022 "));
    }
    li.appendChild(focals);
  }

  if (record.warnings.length) {
    const messages = document.createElement("ul");
    messages.className = "messages";
    for (const [severity, message] of record.warnings) {
      const item = document.createElement("li");
      item.className = severity === ERROR ? "error-line" : "warn-line";
      item.textContent = `${severity === ERROR ? "\u274c" : "\u26a0\ufe0f"} ${message}`;
      messages.appendChild(item);
    }
    li.appendChild(messages);
  }

  return li;
}

function render() {
  // buildNodes sorts records in place, so the DOM order and the combo box order match.
  const nodes = buildNodes(records);

  note.hidden = records.length === 0;
  list.textContent = "";
  for (const record of records) list.appendChild(rowFor(record));

  const usable = records.filter((r) => r.nodes.length).length;
  summary.textContent = nodes.length
    ? `${usable} of ${records.length} profiles \u2192 ${nodes.length} node${nodes.length === 1 ? "" : "s"}`
    : `${records.length} profile${records.length === 1 ? "" : "s"} loaded, none usable`;

  downloadButton.disabled = nodes.length === 0;
}

function download() {
  const blob = new Blob([makeDCTL(buildNodes(records))], {
    type: "text/plain",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = OUTPUT_NAME;
  link.click();
  URL.revokeObjectURL(url);
}

fileInput.addEventListener("change", (event) => addFiles(event.target.files));

for (const path of document.querySelectorAll(".copy")) {
  path.addEventListener("click", () => {
    navigator.clipboard
      .writeText(path.textContent.replace(/\s+/g, " ").trim())
      .then(() => {
        path.classList.add("copied");
        setTimeout(() => path.classList.remove("copied"), 1200);
      });
  });
}

dropZone.addEventListener("click", () => fileInput.click());

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () =>
  dropZone.classList.remove("dragging"),
);

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  addFiles(event.dataTransfer.files);
});

downloadButton.addEventListener("click", download);
