import math
import os
import re
from collections import Counter, defaultdict
from xml.parsers.expat import ExpatError

import xmltodict

# Sanity band for the vignette polynomial, same as autoshade: nodes outside it are junk
VIGNETTE_V_MIN = 0.05
VIGNETTE_V_MAX = 20.0
VIGNETTE_SCAN_SAMPLES = 100

DCTL_LOCAL_PATH = "Vignette Compensation.dctl"
DCTL_RESOLVE_PATH = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\DCTL\Vignette Compensation.dctl"

ERROR = "error"
WARN = "warning"

MSG_NOT_LCP = "Not a valid LCP file"
MSG_NO_VIGNETTE = "No vignette model in profile"
MSG_NO_FOCUS = "Vignette entry has no focus distance or aperture"
MSG_NO_FX = "Vignette model has no FocalLengthX"
MSG_NO_FOCAL = "FocalLength missing \u2014 node named from profile name"
MSG_PARTIAL_VIGNETTE = "No vignette model for some focal lengths"
MSG_DEGENERATE = "Degenerate vignette polynomial for some focal lengths"
MSG_NO_SFF = "SensorFormatFactor missing \u2014 assumed 1.0"
MSG_NO_DIMS = "Image dimensions missing \u2014 assumed 3:2 frame"

# A prime carries one true focal length (measured at infinity focus), repeated as
# slightly different effective values per focus distance. When every focal length in
# a profile sits within this ratio of the largest one, it is a single prime.
PRIME_SPREAD_RATIO = 0.10

# Sensor badge bands. SFF is written to the DCTL rounded to one decimal, so the
# badge is derived from the same rounded value and can never disagree with it.
SFF_FULL_FRAME_MAX = 1.05
SFF_APSC_MAX = 1.8
SFF_MFT_MAX = 2.2


def roundFocal(value):
    # Mirror JS Math.round: halves always go up, not to even.
    return int(value + 0.5)


def vignetteIsBad(a1, a2, a3, rhoMax):
    rhoStep = rhoMax / VIGNETTE_SCAN_SAMPLES
    rho = 0.0
    while rho <= rhoMax:
        rho2 = rho * rho
        V = 1.0 + rho2 * (a1 + rho2 * (a2 + rho2 * a3))
        # V above 20 is nonsense; V collapsing near the center means the polynomial is junk
        if V > VIGNETTE_V_MAX or (V < VIGNETTE_V_MIN and rho < rhoMax * 0.25):
            return True
        rho += rhoStep
    return False


def sensorBadge(sff):
    if sff is None:
        return "?"
    rounded = round(sff, 1)
    if rounded < 0.95:
        return "MF"
    if rounded <= SFF_FULL_FRAME_MAX:
        return "FF"
    if rounded <= SFF_APSC_MAX:
        return "APS-C"
    if rounded <= SFF_MFT_MAX:
        return "MFT"
    return f"{rounded:g}x"


def vignetteParams(desc):
    """Return (params, perspectiveAttrs) or (None, None) when the entry has no vignette model.

    The model lives either on the PerspectiveModel element itself or on its nested
    rdf:Description, and the params either on the VignetteModel element or on its child.
    perspectiveAttrs is whichever element actually carries FocalLengthX / ImageXCenter.
    """
    perspective = desc.get("stCamera:PerspectiveModel")
    if not isinstance(perspective, dict):
        return None, None

    child = perspective.get("rdf:Description")
    attrs = child if isinstance(child, dict) else perspective

    vignette = perspective.get("@stCamera:VignetteModel")
    if vignette is None and attrs is not perspective:
        vignette = attrs.get("stCamera:VignetteModel")
    if not isinstance(vignette, dict):
        return None, attrs

    params = (
        vignette
        if "@stCamera:VignetteModelParam1" in vignette
        else vignette.get("rdf:Description")
    )
    if not isinstance(params, dict):
        return None, attrs
    return params, attrs


def parseProfile(path, fileName):
    """Parse one .lcp into a display record plus the nodes it contributes."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            profileData = xmltodict.parse(f.read())
        rdf = profileData["x:xmpmeta"]["rdf:RDF"]["rdf:Description"]
        if isinstance(rdf, list):
            rdf = next(d for d in rdf if "photoshop:CameraProfiles" in d)
        seq = rdf["photoshop:CameraProfiles"]["rdf:Seq"]["rdf:li"]
    except (ExpatError, KeyError, StopIteration, UnicodeDecodeError):
        return {
            "file": fileName,
            "name": fileName,
            "badge": "?",
            "warnings": [(ERROR, MSG_NOT_LCP)],
            "focals": [],
            "nodes": [],
        }

    if not isinstance(seq, list):
        seq = [seq]

    # Key is the focal length in mm when the profile states one, otherwise FocalLengthX
    # (focal length in image-width units), which is the only focal identifier left.
    groups = {}
    allFocals = set()
    name = ""
    camera = ""
    sff = None
    hasVignette = False
    missingFocus = 0
    missingFx = 0

    for entry in seq:
        desc = entry.get("rdf:Description") if isinstance(entry, dict) else None
        if not isinstance(desc, dict):
            continue

        if not name:
            name = desc.get("@stCamera:LensPrettyName") or desc.get(
                "@stCamera:ProfileName", ""
            )
        if not camera:
            camera = desc.get("@stCamera:CameraPrettyName", "")
        if sff is None:
            sff = desc.get("@stCamera:SensorFormatFactor")

        model, perspective = vignetteParams(desc)
        rawFocal = desc.get("@stCamera:FocalLength")
        if rawFocal is not None:
            allFocals.add(float(rawFocal))
        if model is None or perspective is None:
            continue
        hasVignette = True

        focus = desc.get("@stCamera:FocusDistance")
        aperture = desc.get("@stCamera:ApertureValue")
        if focus is None or aperture is None:
            missingFocus += 1
            continue

        fx = model.get("@stCamera:FocalLengthX") or perspective.get(
            "@stCamera:FocalLengthX"
        )
        if rawFocal is None:
            if fx is None:
                missingFx += 1
                continue
            key = float(fx)
            hasFocal = False
        else:
            key = float(rawFocal)
            hasFocal = True
        if key in groups:
            groups[key][1].append((float(focus), float(aperture), desc))
        else:
            groups[key] = [hasFocal, [(float(focus), float(aperture), desc)]]

    warnings = []
    focals = []
    nodes = []
    degenerate = False
    missingDims = 0
    focalLess = False
    usableFocals = {}

    # A prime is one focal length sampled at several focus distances: when every
    # focal value in the profile sits within PRIME_SPREAD_RATIO of the largest one,
    # they are all the same focal length and collapse into a single node.
    keys = sorted(groups)
    if len(keys) > 1 and (keys[-1] - keys[0]) / keys[-1] < PRIME_SPREAD_RATIO:
        clusters = [keys]
    else:
        # Otherwise each focal value is a distinct zoom position; positions that
        # round to the same integer share one node.
        byFocal = {}
        for key in keys:
            byFocal.setdefault(roundFocal(key), []).append(key)
        clusters = [byFocal[r] for r in sorted(byFocal)]

    for cluster in clusters:
        key = cluster[-1]
        label = f"{roundFocal(key)}mm" if any(groups[k][0] for k in cluster) else None
        entries = [e for k in cluster for e in groups[k][1]]
        # widest aperture at infinity focus
        focus, aperture, desc = max(entries, key=lambda t: (t[0], -t[1]))
        model, perspective = vignetteParams(desc)
        if model is None or perspective is None:
            continue

        a1 = float(model["@stCamera:VignetteModelParam1"])
        a2 = float(model.get("@stCamera:VignetteModelParam2", 0.0))
        a3 = float(model.get("@stCamera:VignetteModelParam3", 0.0))

        # FocalLengthX = focal length in image-width units, taken from the vignette block
        # itself: the chromatic sub-models below repeat the attribute with their own values.
        fx = model.get("@stCamera:FocalLengthX") or perspective.get(
            "@stCamera:FocalLengthX"
        )
        nodeSff = float(sff) if sff is not None else 1.0
        if fx is None:
            # A focal-less profile has no focal length to fall back on: FocalLengthX is
            # the only scale it carries, so without it the model cannot be used.
            if label is None:
                continue
            fx = key * nodeSff / 36.0
        fx = float(fx)

        cx = float(perspective.get("@stCamera:ImageXCenter", "0.5"))
        cy = float(perspective.get("@stCamera:ImageYCenter", "0.5"))

        # model is only valid inside the frame: max rho is the image corner (matches the
        # piecewise params in LCP, e.g. 40mm: hypot(2817,1872)/(1.226545*5634) = 0.489)
        imgW = desc.get("@stCamera:ImageWidth")
        imgH = desc.get("@stCamera:ImageLength")
        if imgW is not None and imgH is not None:
            rhoMax = math.hypot(float(imgW) / 2.0, float(imgH) / 2.0) / (
                fx * float(imgW)
            )
        else:
            # some profiles (Sigma) carry no dimensions: assume a 3:2 frame
            rhoMax = 0.625 / fx
            missingDims += 1

        if vignetteIsBad(a1, a2, a3, rhoMax):
            degenerate = True
            continue

        if label is None:
            focalLess = True
        else:
            usableFocals[key] = aperture
        prettyName = f"{name}  \u2014 {label}" if label else name
        nodes.append(
            {
                "code": re.sub(r"[^0-9A-Za-z]", "_", prettyName),
                "display": prettyName,
                "camera": camera,
                "fx": f"{fx:.6f}f",
                "a1": f"{a1}f",
                "a2": f"{a2}f",
                "a3": f"{a3}f",
                "cx": f"{cx:.6f}f",
                "cy": f"{cy:.6f}f",
                "sff": f"{round(nodeSff, 1):.1f}f",
            }
        )

    # The display list mirrors the node clusters; focal lengths listed by the profile
    # without a vignette entry are shown struck out, unless an integer focal length
    # for them already appears.
    reps = set()
    for cluster in clusters:
        key = cluster[-1]
        reps.add(roundFocal(key))
        if key in usableFocals:
            focals.append((key, True, usableFocals[key]))
        else:
            focals.append((key, False, None))
    for focal_mm in sorted(allFocals):
        if roundFocal(focal_mm) in reps:
            continue
        reps.add(roundFocal(focal_mm))
        focals.append((focal_mm, False, None))
    focals.sort(key=lambda t: t[0])

    if not nodes:
        if not hasVignette:
            warnings.append((ERROR, MSG_NO_VIGNETTE))
        elif degenerate:
            warnings.append((ERROR, MSG_DEGENERATE))
        else:
            if missingFocus:
                warnings.append((ERROR, MSG_NO_FOCUS))
            if missingFx:
                warnings.append((ERROR, MSG_NO_FX))
            if not missingFocus and not missingFx:
                warnings.append((ERROR, MSG_NO_VIGNETTE))
    else:
        if any(not ok for _, ok, _ in focals):
            warnings.append((ERROR, MSG_PARTIAL_VIGNETTE))
        if degenerate:
            warnings.append((ERROR, MSG_DEGENERATE))
        if focalLess:
            warnings.append((WARN, MSG_NO_FOCAL))
    if sff is None:
        warnings.append((WARN, MSG_NO_SFF))
    if nodes and missingDims == len(nodes):
        warnings.append((WARN, MSG_NO_DIMS))

    return {
        "file": fileName,
        "name": name or fileName,
        "badge": sensorBadge(float(sff) if sff is not None else None),
        "warnings": warnings,
        "focals": focals,
        "nodes": nodes,
    }


def buildNodes(records):
    # One ordering for the UI and for the DCTL combo box, so row N in the list is
    # item N in NodeSel.
    records.sort(key=lambda r: f"{r['badge']} {r['name'].lower()}")
    nodes = []
    for record in records:
        nodes.extend(record["nodes"])

    counts = Counter(node["code"] for node in nodes)
    seen = defaultdict(int)
    for node in nodes:
        if counts[node["code"]] > 1:
            seen[node["code"]] += 1
            index = seen[node["code"]]
            node["codename"] = f"{node['code']}_{index}"
            if node["camera"]:
                node["display"] = f"{node['display']} ({node['camera']})"
            else:
                node["display"] = f"{node['display']} #{index}"
        else:
            node["codename"] = node["code"]
    return nodes


def printRecords(records):
    for record in records:
        print(f"[{record['badge']}] {record['name']}")
        parts = []
        for focal_mm, ok, aperture in record["focals"]:
            label = f"{roundFocal(focal_mm)}mm" + (f" f/{aperture:.1f}" if ok else "")
            parts.append(label if ok else f"[{label}]")
        if parts:
            print("  " + " \u2022 ".join(parts))
        for severity, message in record["warnings"]:
            mark = "\u274c" if severity == ERROR else "\u26a0\ufe0f"
            print(f"    {mark} {message}")


def makeDCTL(nodes):
    if not nodes:
        raise ValueError("No usable vignette profiles found")

    with open("prefix.c", "r", encoding="utf-8") as f:
        prefix = f.read()
    with open("suffix.c", "r", encoding="utf-8") as f:
        suffix = f.read()
    count = len(nodes)

    codenames = [node["codename"] for node in nodes]
    prettyNames = [node["display"] for node in nodes]

    row_define = f"DEFINE_UI_PARAMS(NodeSel, Profile, DCTLUI_COMBO_BOX, 0, {{{', '.join(codenames)}}}, {{{', '.join(prettyNames)}}})"
    row_nodes = f"static constant int NODES = {count};"
    row_fx = f"static constant float FX[{count}] = {{{', '.join(node['fx'] for node in nodes)}}};"
    row_a1 = f"static constant float A1[{count}] = {{{', '.join(node['a1'] for node in nodes)}}};"
    row_a2 = f"static constant float A2[{count}] = {{{', '.join(node['a2'] for node in nodes)}}};"
    row_a3 = f"static constant float A3[{count}] = {{{', '.join(node['a3'] for node in nodes)}}};"
    row_cx = f"static constant float CX[{count}] = {{{', '.join(node['cx'] for node in nodes)}}};"
    row_cy = f"static constant float CY[{count}] = {{{', '.join(node['cy'] for node in nodes)}}};"
    row_sff = f"static constant float SFF[{count}] = {{{', '.join(node['sff'] for node in nodes)}}};"

    parts = [
        prefix,
        row_define,
        row_nodes,
        row_fx,
        row_a1,
        row_a2,
        row_a3,
        row_cx,
        row_cy,
        row_sff,
        suffix,
    ]
    dctl_code = "\n\n".join(parts)

    with open(DCTL_LOCAL_PATH, "w", encoding="utf-8") as f:
        f.write(dctl_code)

    os.makedirs(os.path.dirname(DCTL_RESOLVE_PATH), exist_ok=True)
    with open(DCTL_RESOLVE_PATH, "w", encoding="utf-8") as f:
        f.write(dctl_code)

    print(f"Wrote {count} profiles to {DCTL_LOCAL_PATH} and {DCTL_RESOLVE_PATH}")


def main():
    records = []
    for profileFile in sorted(os.listdir("lcp")):
        records.append(parseProfile(f"lcp/{profileFile}", profileFile))

    nodes = buildNodes(records)
    printRecords(records)
    makeDCTL(nodes)


if __name__ == "__main__":
    main()
