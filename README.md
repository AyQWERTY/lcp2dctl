# lcp2dctl

Converts Adobe Camera Raw lens profiles into a DaVinci Resolve DCTL that compensates lens vignetting.

The conversion is direct: the vignetting data embedded in an Adobe profile is taken exactly as it is and loaded into the DCTL unchanged. No approximation, no fitting, no reverse engineering. What Lightroom applies to your photo is what this tool applies to your footage.

The output is a single DCTL containing all profiles found in the `lcp` directory, selectable per lens and focal length inside Resolve.

## Setup

1. Place your lens profiles (`.lcp`) into the `lcp` directory. Adobe keeps them at `C:\ProgramData\Adobe\CameraRaw\LensProfiles` (on Windows). Profiles are not included in the repository.
2. Install Python and `xmltodict` (e.g. `uv venv && uv pip install xmltodict`).
3. Run `python main.py`. The generated `Vignette Compensation.dctl` is written next to the script and directly into the Resolve DCTL folder (`C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT\DCTL`), replacing the previous version.

## How to use in Resolve

- Add the DCTL as a node on the color page.
- Place it before any grading: **Group pre-clip/Clip**. The correction must be applied to the untouched image, not after color.
- Set the node to **linear gamma**: right click the node → Gamma → Linear. Without this the correction will be wrong.

## Controls

- **Profile** — lens and focal length preset, built from your own profiles.
- **Footage** — sensor format the clip was shot in: Full Frame, APS-C 1.52, APS-C 1.61, Micro Four Thirds. Scales the profile to the capture format.
- **Blend** — correction amount. 1.0 is the full Adobe correction, 0.0 is none.

Profiles containing invalid data are skipped during generation and reported on the console.
