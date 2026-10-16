import math
import os
import re
from collections import defaultdict

import xmltodict

# Sanity band for the vignette polynomial, same as autoshade: nodes outside it are junk
VIGNETTE_V_MIN = 0.05
VIGNETTE_V_MAX = 20.0
VIGNETTE_SCAN_SAMPLES = 100

DCTL_LOCAL_PATH = "Vignette Compensation.dctl"
DCTL_RESOLVE_PATH = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\DCTL\Vignette Compensation.dctl"

lensCodename = []
lensPrettyName = []
lensFx = []
lensA1 = []
lensA2 = []
lensA3 = []
lensCx = []
lensCy = []
lensSff = []


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


def processLCP(profileData, sffDefault):
    groups = defaultdict(list)

    for entry in profileData:
        desc = entry["rdf:Description"]
        focal = float(desc["@stCamera:FocalLength"])
        focus = float(desc["@stCamera:FocusDistance"])
        aperture = float(desc["@stCamera:ApertureValue"])

        groups[focal].append((focus, aperture, desc))

    for focal_mm, entries in groups.items():
        # widest aperture at infinity focus
        focus, aperture, desc = max(entries, key=lambda t: (t[0], -t[1]))

        perspective = desc.get("stCamera:PerspectiveModel", {}).get("rdf:Description")
        vignette = perspective.get("stCamera:VignetteModel")
        if vignette is None:
            print(f"Skipping '{desc['@stCamera:ProfileName']}': no vignette model")
            continue

        model = (
            vignette
            if "@stCamera:VignetteModelParam1" in vignette
            else vignette["rdf:Description"]
        )

        a1 = float(model["@stCamera:VignetteModelParam1"])
        a2 = float(model["@stCamera:VignetteModelParam2"])
        a3 = float(model["@stCamera:VignetteModelParam3"])

        # FocalLengthX = focal length in image-width units, taken from the vignette block
        # itself: the chromatic sub-models below repeat the attribute with their own values.
        fx = model.get("@stCamera:FocalLengthX") or perspective.get(
            "@stCamera:FocalLengthX"
        )
        sff = float(desc.get("@stCamera:SensorFormatFactor", sffDefault))
        if fx is None:
            fx = focal_mm * sff / 36.0
        fx = float(fx)

        cx = float(perspective.get("@stCamera:ImageXCenter", "0.5"))
        cy = float(perspective.get("@stCamera:ImageYCenter", "0.5"))

        # model is only valid inside the frame: max rho is the image corner (matches the
        # piecewise params in LCP, e.g. 40mm: hypot(2817,1872)/(1.226545*5634) = 0.489)
        imgW = desc.get("@stCamera:ImageWidth")
        imgH = desc.get("@stCamera:ImageLength")
        if imgW is not None:
            rhoMax = math.hypot(float(imgW) / 2.0, float(imgH) / 2.0) / (
                fx * float(imgW)
            )
        else:
            # some profiles (Sigma) carry no dimensions: assume a 3:2 frame
            rhoMax = 0.625 / fx

        if vignetteIsBad(a1, a2, a3, rhoMax):
            print(
                f"Skipping degenerate node: {desc['@stCamera:ProfileName']} (a1={a1}, a2={a2}, a3={a3})"
            )
            continue

        prettyName = f"{desc['@stCamera:LensPrettyName']}  — {focal_mm:g}mm"
        codename = re.sub(r"[^0-9A-Za-z]", "_", prettyName)

        lensCodename.append(codename)
        lensPrettyName.append(prettyName)
        lensFx.append(f"{fx:.6f}f")
        lensA1.append(f"{a1}f")
        lensA2.append(f"{a2}f")
        lensA3.append(f"{a3}f")
        lensCx.append(f"{cx:.6f}f")
        lensCy.append(f"{cy:.6f}f")
        lensSff.append(f"{round(sff, 1):.1f}f")


def makeDCTL():
    lengths = {
        "lensCodename": len(lensCodename),
        "lensPrettyName": len(lensPrettyName),
        "lensFx": len(lensFx),
        "lensA1": len(lensA1),
        "lensA2": len(lensA2),
        "lensA3": len(lensA3),
        "lensCx": len(lensCx),
        "lensCy": len(lensCy),
        "lensSff": len(lensSff),
    }

    if len(set(lengths.values())) != 1:
        raise ValueError(f"Data length mismatch: {lengths}")

    with open("prefix.c", "r", encoding="utf-8") as f:
        prefix = f.read()
    with open("suffix.c", "r", encoding="utf-8") as f:
        suffix = f.read()
    nodes = len(lensCodename)

    row_define = f"DEFINE_UI_PARAMS(NodeSel, Profile, DCTLUI_COMBO_BOX, 0, {{{', '.join(lensCodename)}}}, {{{', '.join(lensPrettyName)}}})"
    row_nodes = f"static constant int NODES = {nodes};"
    row_fx = f"static constant float FX[{nodes}] = {{{', '.join(lensFx)}}};"
    row_a1 = f"static constant float A1[{nodes}] = {{{', '.join(lensA1)}}};"
    row_a2 = f"static constant float A2[{nodes}] = {{{', '.join(lensA2)}}};"
    row_a3 = f"static constant float A3[{nodes}] = {{{', '.join(lensA3)}}};"
    row_cx = f"static constant float CX[{nodes}] = {{{', '.join(lensCx)}}};"
    row_cy = f"static constant float CY[{nodes}] = {{{', '.join(lensCy)}}};"
    row_sff = f"static constant float SFF[{nodes}] = {{{', '.join(lensSff)}}};"

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

    print(f"Wrote {nodes} profiles to {DCTL_LOCAL_PATH} and {DCTL_RESOLVE_PATH}")


def main():
    lcpProfiles = os.listdir("lcp")

    for profileFile in lcpProfiles:
        path = f"lcp/{profileFile}"
        with open(path, "r", encoding="utf-8") as f:
            profileData = xmltodict.parse(f.read())

        rdf = profileData["x:xmpmeta"]["rdf:RDF"]["rdf:Description"]
        if isinstance(rdf, list):
            rdf = next(d for d in rdf if "photoshop:CameraProfiles" in d)
        seq = rdf["photoshop:CameraProfiles"]["rdf:Seq"]["rdf:li"]
        if not isinstance(seq, list):
            seq = [seq]

        first = seq[0]["rdf:Description"]
        processLCP(seq, first.get("@stCamera:SensorFormatFactor", "1.0"))

    makeDCTL()


if __name__ == "__main__":
    main()
