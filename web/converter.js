// LCP -> DCTL converter. Mirrors main.py exactly: same node order, same codenames,
// same warning wording, so the UI list and the NodeSel combo box always match.

const VIGNETTE_V_MIN = 0.05;
const VIGNETTE_V_MAX = 20.0;
const VIGNETTE_SCAN_SAMPLES = 100;

// A prime carries one true focal length (measured at infinity focus), repeated as
// slightly different effective values per focus distance. When every focal length in
// a profile sits within this ratio of the largest one, it is a single prime.
const PRIME_SPREAD_RATIO = 0.1;

// Sensor badge bands. SFF is written to the DCTL rounded to one decimal, so the
// badge is derived from the same rounded value and can never disagree with it.
const SFF_FULL_FRAME_MAX = 1.05;
const SFF_APSC_MAX = 1.8;
const SFF_MFT_MAX = 2.2;

export const ERROR = "error";
export const WARN = "warning";

export const MSG = {
  notLcp: "Not a valid LCP file",
  noVignette: "No vignette model in profile",
  noFocus: "Vignette entry has no focus distance or aperture",
  noFx: "Vignette model has no FocalLengthX",
  noFocal: "FocalLength missing \u2014 node named from profile name",
  partialVignette: "No vignette model for some focal lengths",
  degenerate: "Degenerate vignette polynomial for some focal lengths",
  noSff: "SensorFormatFactor missing \u2014 assumed 1.0",
  noDims: "Image dimensions missing \u2014 assumed 3:2 frame",
};

const DCTL_PREFIX = `DEFINE_UI_PARAMS(Blend, Blend, DCTLUI_SLIDER_FLOAT, 1.00, 0.00, 1.00, 0.01)
DEFINE_UI_PARAMS(FootageSel, Footage, DCTLUI_COMBO_BOX, 0, {FF, APSC15, APSC16, MFT}, {Full Frame, APS-C 1.52, APS-C 1.61, Micro Four Thirds})
`;

const DCTL_SUFFIX = `static constant float SENS[4] = {1.0f, 1.52f, 1.61f, 2.0f};

__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, float p_R, float p_G, float p_B) {
    int i = (int)NodeSel; if (i < 0) i = 0; if (i >= NODES) i = NODES-1;
    int s = (int)FootageSel; if (s < 0) s = 0; if (s > 3) s = 3;

    float W = (float)p_Width;
    float H = (float)p_Height;
    // scale the calibration grid by footage_format / profile_format
    float D = FX[i] * W * SENS[s] / SFF[i];

    float cx = CX[i] * (W - 1.0f);
    float cy = CY[i] * (H - 1.0f);
    float dx = (float)p_X - cx;
    float dy = (float)p_Y - cy;

    float rho2 = (dx*dx + dy*dy) / (D*D);
    float V = 1.0f + rho2 * (A1[i] + rho2 * (A2[i] + rho2 * A3[i]));
    // Trusted band of the model: gain capped at 20x. Near the extreme corner
    // of wide lenses the polynomial overshoots toward zero.
    if (V < 0.05f) V = 0.05f;
    if (V > 20.0f) V = 20.0f;

    float t = _clampf(Blend, 0.0f, 1.0f);
    float gain = 1.0f + t * (1.0f / V - 1.0f);

    return make_float3(p_R * gain, p_G * gain, p_B * gain);
}
`;

function directChild(element, name) {
  for (const child of element.children) {
    if (child.tagName === name) return child;
  }
  return null;
}

function attr(element, name) {
  return element.hasAttribute(name) ? element.getAttribute(name) : null;
}

// Sanity band for the vignette polynomial, same as autoshade: nodes outside it are junk.
export function vignetteIsBad(a1, a2, a3, rhoMax) {
  const rhoStep = rhoMax / VIGNETTE_SCAN_SAMPLES;
  let rho = 0.0;
  while (rho <= rhoMax) {
    const rho2 = rho * rho;
    const v = 1.0 + rho2 * (a1 + rho2 * (a2 + rho2 * a3));
    // V above 20 is nonsense; V collapsing near the center means the polynomial is junk.
    if (v > VIGNETTE_V_MAX || (v < VIGNETTE_V_MIN && rho < rhoMax * 0.25))
      return true;
    rho += rhoStep;
  }
  return false;
}

export function sensorBadge(sff) {
  if (sff === null) return "?";
  const rounded = round1(sff);
  if (rounded < 0.95) return "MF";
  if (rounded <= SFF_FULL_FRAME_MAX) return "FF";
  if (rounded <= SFF_APSC_MAX) return "APS-C";
  if (rounded <= SFF_MFT_MAX) return "MFT";
  return `${rounded}x`;
}

// Returns [paramsElement, attrsElement] or [null, null] when the entry has no vignette
// model. The model lives either on the PerspectiveModel element itself or on its nested
// rdf:Description; the params either on the VignetteModel element or on its child.
function vignetteParams(desc) {
  const perspective = directChild(desc, "stCamera:PerspectiveModel");
  if (!perspective) return [null, null];

  const child = directChild(perspective, "rdf:Description");
  const attrs = child || perspective;

  let vignette = directChild(attrs, "stCamera:VignetteModel");
  if (!vignette && attrs !== perspective) {
    vignette = directChild(perspective, "stCamera:VignetteModel");
  }
  if (!vignette) return [null, attrs];

  const params = vignette.hasAttribute("stCamera:VignetteModelParam1")
    ? vignette
    : directChild(vignette, "rdf:Description");
  if (!params) return [null, attrs];
  return [params, attrs];
}

function errorRecord(fileName) {
  return {
    file: fileName,
    name: fileName,
    badge: "?",
    warnings: [[ERROR, MSG.notLcp]],
    focals: [],
    nodes: [],
  };
}

// Mirrors Python's float(): it accepts "inf", which Number() does not.
function parseNumber(value) {
  if (value === null) return null;
  const text = String(value).trim().toLowerCase();
  if (text === "inf" || text === "infinity") return Infinity;
  if (text === "-inf" || text === "-infinity") return -Infinity;
  return Number(value);
}

// Mirrors Python's int(x + 0.5) in main.py: halves round up, not to even.
function roundFocal(value) {
  return Math.floor(value + 0.5);
}

// Mirrors Python's str(float): integral values keep a trailing ".0", and small
// values switch to exponent notation (1e-05, not 0.00001) like Python's repr.
function formatFloat(value) {
  if (Number.isInteger(value)) return `${value}.0`;
  const text = String(value);
  const match = /^(-?)0\.(0+)(\d+)$/.exec(text);
  if (match && match[2].length >= 4) {
    const magnitude = match[2].length + 1;
    const mantissa =
      match[3].length > 1 ? `${match[3][0]}.${match[3].slice(1)}` : match[3];
    return `${match[1]}${mantissa}e-${magnitude < 10 ? `0${magnitude}` : magnitude}`;
  }
  return text;
}

// Mirrors Python's round(x, 1). toFixed(1) rounds the exact double value, while
// Math.round(x * 10) first rounds the product (3.55 -> 35.5 -> 3.6).
function round1(value) {
  return Number(value.toFixed(1));
}

export function parseLcp(text, fileName) {
  let doc;
  try {
    doc = new DOMParser().parseFromString(text, "application/xml");
  } catch {
    return errorRecord(fileName);
  }
  if (doc.getElementsByTagName("parsererror").length)
    return errorRecord(fileName);

  let profileDesc = null;
  for (const desc of doc.getElementsByTagName("rdf:Description")) {
    if (desc.getElementsByTagName("photoshop:CameraProfiles").length) {
      profileDesc = desc;
      break;
    }
  }
  if (!profileDesc) return errorRecord(fileName);

  // Sort key is the focal length in mm when the profile states one, otherwise
  // FocalLengthX (focal length in image-width units), the only focal identifier left.
  const groups = new Map(); // key -> { hasFocal, entries }
  const allFocals = new Set();
  let name = "";
  let camera = "";
  let sff = null;
  let hasVignette = false;
  let missingFocus = 0;
  let missingFx = 0;

  for (const li of profileDesc.getElementsByTagName("rdf:li")) {
    const desc = directChild(li, "rdf:Description");
    if (!desc) continue;

    if (!name)
      name =
        attr(desc, "stCamera:LensPrettyName") ||
        attr(desc, "stCamera:ProfileName") ||
        "";
    if (!camera) camera = attr(desc, "stCamera:CameraPrettyName") || "";
    if (sff === null) sff = attr(desc, "stCamera:SensorFormatFactor");

    const [model, perspective] = vignetteParams(desc);
    const rawFocal = attr(desc, "stCamera:FocalLength");
    if (rawFocal !== null) allFocals.add(parseNumber(rawFocal));
    if (!model) continue;
    hasVignette = true;

    const focus = attr(desc, "stCamera:FocusDistance");
    const aperture = attr(desc, "stCamera:ApertureValue");
    if (focus === null || aperture === null) {
      missingFocus += 1;
      continue;
    }

    // FocalLengthX = focal length in image-width units, taken from the vignette block
    // itself: the chromatic sub-models below repeat the attribute with their own values.
    const fx =
      attr(model, "stCamera:FocalLengthX") ||
      attr(perspective, "stCamera:FocalLengthX");
    if (rawFocal === null && fx === null) {
      missingFx += 1;
      continue;
    }

    const key = rawFocal !== null ? parseNumber(rawFocal) : parseNumber(fx);
    if (!groups.has(key))
      groups.set(key, { hasFocal: rawFocal !== null, entries: [] });
    groups.get(key).entries.push({
      focus: parseNumber(focus),
      aperture: parseNumber(aperture),
      desc,
    });
  }

  const warnings = [];
  const focals = [];
  const nodes = [];
  const usableFocals = new Map();
  let degenerate = false;
  let missingDims = 0;
  let focalLess = false;

  // A prime is one focal length sampled at several focus distances: when every
  // focal value in the profile sits within PRIME_SPREAD_RATIO of the largest one,
  // they are all the same focal length and collapse into a single node.
  const keys = [...groups.keys()].sort((a, b) => a - b);
  const isPrime =
    keys.length > 1 &&
    (keys[keys.length - 1] - keys[0]) / keys[keys.length - 1] <
      PRIME_SPREAD_RATIO;
  let clusters;
  if (isPrime) {
    clusters = [keys];
  } else {
    // Otherwise each focal value is a distinct zoom position; positions that
    // round to the same integer share one node.
    const byFocal = new Map();
    for (const key of keys) {
      const r = roundFocal(key);
      if (!byFocal.has(r)) byFocal.set(r, []);
      byFocal.get(r).push(key);
    }
    clusters = [...byFocal.values()];
  }

  for (const cluster of clusters) {
    const key = cluster[cluster.length - 1];
    const label = cluster.some((k) => groups.get(k).hasFocal)
      ? `${roundFocal(key)}mm`
      : null;
    const entries = cluster.flatMap((k) => groups.get(k).entries);

    // widest aperture at infinity focus
    let chosen = entries[0];
    for (const entry of entries) {
      if (
        entry.focus > chosen.focus ||
        (entry.focus === chosen.focus && entry.aperture < chosen.aperture)
      ) {
        chosen = entry;
      }
    }

    const [model, perspective] = vignetteParams(chosen.desc);
    if (!model || !perspective) continue;

    const a1 = parseNumber(attr(model, "stCamera:VignetteModelParam1"));
    const a2 = parseNumber(attr(model, "stCamera:VignetteModelParam2") || "0");
    const a3 = parseNumber(attr(model, "stCamera:VignetteModelParam3") || "0");

    const nodeSff = sff !== null ? parseNumber(sff) : 1.0;
    let fx =
      attr(model, "stCamera:FocalLengthX") ||
      attr(perspective, "stCamera:FocalLengthX");
    if (fx === null) {
      // A focal-less profile has no focal length to fall back on: FocalLengthX is
      // the only scale it carries, so without it the model cannot be used.
      if (label === null) continue;
      fx = (key * nodeSff) / 36.0;
    }
    fx = parseNumber(fx);

    const cx = parseNumber(attr(perspective, "stCamera:ImageXCenter") || "0.5");
    const cy = parseNumber(attr(perspective, "stCamera:ImageYCenter") || "0.5");

    // model is only valid inside the frame: max rho is the image corner
    const imgW = attr(chosen.desc, "stCamera:ImageWidth");
    const imgH = attr(chosen.desc, "stCamera:ImageLength");
    let rhoMax;
    if (imgW !== null && imgH !== null) {
      rhoMax =
        Math.hypot(parseNumber(imgW) / 2.0, parseNumber(imgH) / 2.0) /
        (fx * parseNumber(imgW));
    } else {
      // some profiles (Sigma) carry no dimensions: assume a 3:2 frame
      rhoMax = 0.625 / fx;
      missingDims += 1;
    }

    if (vignetteIsBad(a1, a2, a3, rhoMax)) {
      degenerate = true;
      continue;
    }

    if (label === null) focalLess = true;
    else usableFocals.set(key, chosen.aperture);

    const prettyName = label ? `${name}  \u2014 ${label}` : name;
    nodes.push({
      code: prettyName.replace(/[^0-9A-Za-z]/g, "_"),
      display: prettyName,
      camera,
      fx: `${fx.toFixed(6)}f`,
      a1: `${formatFloat(a1)}f`,
      a2: `${formatFloat(a2)}f`,
      a3: `${formatFloat(a3)}f`,
      cx: `${cx.toFixed(6)}f`,
      cy: `${cy.toFixed(6)}f`,
      sff: `${nodeSff.toFixed(1)}f`,
    });
  }

  // The display list mirrors the node clusters; focal lengths listed by the profile
  // without a vignette entry are shown struck out, unless an integer focal length
  // for them already appears.
  const reps = new Set();
  for (const cluster of clusters) {
    const key = cluster[cluster.length - 1];
    reps.add(roundFocal(key));
    const aperture = usableFocals.get(key);
    focals.push({
      focal: key,
      usable: aperture !== undefined,
      aperture: aperture === undefined ? null : aperture,
    });
  }
  for (const focal of [...allFocals].sort((a, b) => a - b)) {
    if (reps.has(roundFocal(focal))) continue;
    reps.add(roundFocal(focal));
    focals.push({ focal, usable: false, aperture: null });
  }
  focals.sort((a, b) => a.focal - b.focal);

  if (!nodes.length) {
    if (!hasVignette) {
      warnings.push([ERROR, MSG.noVignette]);
    } else if (degenerate) {
      warnings.push([ERROR, MSG.degenerate]);
    } else {
      if (missingFocus) warnings.push([ERROR, MSG.noFocus]);
      if (missingFx) warnings.push([ERROR, MSG.noFx]);
      if (!missingFocus && !missingFx) warnings.push([ERROR, MSG.noVignette]);
    }
  } else {
    if (focals.some((f) => !f.usable))
      warnings.push([ERROR, MSG.partialVignette]);
    if (degenerate) warnings.push([ERROR, MSG.degenerate]);
    if (focalLess) warnings.push([WARN, MSG.noFocal]);
  }
  if (sff === null) warnings.push([WARN, MSG.noSff]);
  if (nodes.length && missingDims === nodes.length)
    warnings.push([WARN, MSG.noDims]);

  return {
    file: fileName,
    name: name || fileName,
    badge: sensorBadge(sff !== null ? parseNumber(sff) : null),
    warnings,
    focals,
    nodes,
  };
}

// One ordering for the UI and for the DCTL combo box, so row N in the list is
// item N in NodeSel.
export function buildNodes(records) {
  records.sort((a, b) => {
    const ka = `${a.badge} ${a.name.toLowerCase()}`;
    const kb = `${b.badge} ${b.name.toLowerCase()}`;
    if (ka < kb) return -1;
    if (ka > kb) return 1;
    return 0;
  });

  const nodes = [];
  for (const record of records) nodes.push(...record.nodes);

  const counts = new Map();
  for (const node of nodes)
    counts.set(node.code, (counts.get(node.code) || 0) + 1);

  const seen = new Map();
  for (const node of nodes) {
    if (counts.get(node.code) > 1) {
      const index = (seen.get(node.code) || 0) + 1;
      seen.set(node.code, index);
      node.codename = `${node.code}_${index}`;
      node.display = node.camera
        ? `${node.display} (${node.camera})`
        : `${node.display} #${index}`;
    } else {
      node.codename = node.code;
    }
  }
  return nodes;
}

export function makeDCTL(nodes) {
  if (!nodes.length) throw new Error("No usable vignette profiles found");

  const count = nodes.length;
  const rows = [
    `DEFINE_UI_PARAMS(NodeSel, Profile, DCTLUI_COMBO_BOX, 0, {${nodes.map((n) => n.codename).join(", ")}}, {${nodes.map((n) => n.display).join(", ")}})`,
    `static constant int NODES = ${count};`,
    `static constant float FX[${count}] = {${nodes.map((n) => n.fx).join(", ")}};`,
    `static constant float A1[${count}] = {${nodes.map((n) => n.a1).join(", ")}};`,
    `static constant float A2[${count}] = {${nodes.map((n) => n.a2).join(", ")}};`,
    `static constant float A3[${count}] = {${nodes.map((n) => n.a3).join(", ")}};`,
    `static constant float CX[${count}] = {${nodes.map((n) => n.cx).join(", ")}};`,
    `static constant float CY[${count}] = {${nodes.map((n) => n.cy).join(", ")}};`,
    `static constant float SFF[${count}] = {${nodes.map((n) => n.sff).join(", ")}};`,
  ];

  return [DCTL_PREFIX, ...rows, DCTL_SUFFIX].join("\n\n");
}
