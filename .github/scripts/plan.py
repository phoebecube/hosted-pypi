#!/usr/bin/env python3
"""
Resolve upstream versions and decide which Windows wheels still need building.

Nothing is pinned in this repository: every run asks upstream for
  - the newest 3 stable CPython minors available to GitHub Actions on Windows x64
  - ta-lib (PyPI) + TA-Lib C library (latest git tag)
  - SageAttention (latest woct0rdho/SageAttention `v*-windows*` tag)
  - torch (PyPI latest) and the `cuXXX` variants it ships for Windows
  - the newest CUDA toolkit patch release matching each `cuXXX`
then compares the expected wheels with the assets already attached to this
repository's GitHub Release, so only missing combinations are built.

Usage:  plan.py talib|sageattention
Env:    GITHUB_REPOSITORY, GITHUB_TOKEN, FORCE=true|false,
        SAGE_ARCHS (default "8.0 8.6 8.9 9.0 12.0")
Writes `build`, `matrix`, `version`, `tag` (+ package specific keys) to GITHUB_OUTPUT.
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

UA = {"User-Agent": "hosted-pypi-planner"}
PY_MANIFEST = "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"
CUDA_REDIST = "https://developer.download.nvidia.com/compute/cuda/redist/"
TORCH_INDEX = "https://download.pytorch.org/whl/torch/"
SAGE_REPO = "woct0rdho/SageAttention"
TALIB_C_REPO = "TA-Lib/ta-lib"
TALIB_PY_REPO = "TA-Lib/ta-lib-python"

# Minimum CUDA toolkit for each compute capability (from SageAttention's setup.py).
ARCH_MIN_CUDA = {"8.9": (12, 4), "9.0": (12, 3), "12.0": (12, 8), "12.1": (12, 9)}


def get(url, token=None, accept=None):
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read().decode("utf-8")


def get_json(url, token=None):
    return json.loads(get(url, token, "application/vnd.github+json" if "api.github.com" in url else None))


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def git_tags(repo):
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", f"https://github.com/{repo}.git"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]


def python_minors(n=3):
    """Newest n stable CPython minors that actions/setup-python can install on Windows x64."""
    minors = set()
    for rel in get_json(PY_MANIFEST):
        if not rel.get("stable"):
            continue
        if not any(f.get("platform") == "win32" and f.get("arch") == "x64" for f in rel.get("files", [])):
            continue
        major, minor = rel["version"].split(".")[:2]
        if major == "3":
            minors.add(int(minor))
    top = sorted(minors)[-n:]
    if len(top) < n:
        sys.exit(f"expected {n} Python minors, got {top}")
    return [f"3.{m}" for m in top]


def release_assets(tag):
    """Asset names of this repo's Release `tag` (empty if it does not exist)."""
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_TOKEN")
    try:
        rel = get_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return set()
        raise
    names, page = set(), 1
    while True:
        batch = get_json(f"{rel['assets_url']}?per_page=100&page={page}", token)
        names.update(a["name"] for a in batch)
        if len(batch) < 100:
            return names
        page += 1


def cp(py):
    return "cp" + py.replace(".", "")


# ── TA-Lib ────────────────────────────────────────────────────────────────────
def plan_talib(force):
    version = get_json("https://pypi.org/pypi/ta-lib/json")["info"]["version"]
    if f"v{version}" not in git_tags(TALIB_PY_REPO):
        sys.exit(f"{TALIB_PY_REPO} has no tag v{version}")
    c_tags = [t[1:] for t in git_tags(TALIB_C_REPO) if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    c_version = max(c_tags, key=vkey)

    tag = f"talib-v{version}"
    have = set() if force else release_assets(tag)
    matrix = []
    for py in python_minors():
        wheel = f"ta_lib-{version}-{cp(py)}-{cp(py)}-win_amd64.whl"
        if wheel not in have:
            matrix.append({"python": py, "wheel": wheel})
    return {"version": version, "c_version": c_version, "tag": tag}, matrix


# ── SageAttention ─────────────────────────────────────────────────────────────
def sage_version():
    """Latest `vX.Y.Z-windows[.postN]` tag -> (tag, 'X.Y.Z[.postN]', '[.postN]')."""
    best = None
    for t in git_tags(SAGE_REPO):
        m = re.fullmatch(r"v(\d+\.\d+\.\d+)-windows(?:\.post(\d+))?", t)
        if not m:
            continue
        key = (vkey(m[1]), int(m[2] or -1))
        if best is None or key > best[0]:
            post = f".post{m[2]}" if m[2] else ""
            best = (key, t, m[1] + post, post)
    if best is None:
        sys.exit(f"no v*-windows tag in {SAGE_REPO}")
    return best[1:]


def torch_cuda_variants():
    """Latest torch on PyPI -> (version, {cuXXX: [python minors with a win_amd64 wheel]})."""
    version = get_json("https://pypi.org/pypi/torch/json")["info"]["version"]
    variants = {}
    pat = re.compile(rf"torch-{re.escape(version)}\+(cu\d+)-cp3(\d+)-cp3\2-win_amd64\.whl")
    for cu, minor in set(pat.findall(get(TORCH_INDEX))):
        variants.setdefault(cu, set()).add(f"3.{minor}")
    if not variants:
        sys.exit(f"no Windows CUDA wheels for torch {version}")
    return version, variants


def cuda_toolkit(cu, redist_index):
    """cu130 -> newest '13.0.N' that NVIDIA publishes a redistrib manifest for."""
    digits = cu[2:]
    major, minor = digits[:2], digits[2:]
    found = re.findall(rf"redistrib_({major}\.{minor}\.\d+)\.json", redist_index)
    if not found:
        sys.exit(f"no CUDA redist for {cu}")
    return max(set(found), key=vkey)


def plan_sageattention(force):
    git_tag, version, post = sage_version()
    setup_py = get(f"https://raw.githubusercontent.com/{SAGE_REPO}/{git_tag}/setup.py")
    abi3 = "py_limited_api" in setup_py
    torch_version, variants = torch_cuda_variants()
    pythons = python_minors()
    redist_index = get(CUDA_REDIST)
    archs = os.environ.get("SAGE_ARCHS", "").split() or ["8.0", "8.6", "8.9", "9.0", "12.0"]

    tag = f"sageattention-v{version}"
    have = set() if force else release_assets(tag)
    matrix = []
    for cu in sorted(variants, key=vkey):
        cuda = cuda_toolkit(cu, redist_index)
        arch_list = " ".join(a for a in archs if vkey(cuda)[:2] >= ARCH_MIN_CUDA.get(a, (0, 0)))
        usable = [py for py in pythons if py in variants[cu]]
        if not usable:
            continue
        wheels = [f"sageattention-{version}+{cu}-{cp(py)}-{cp(py)}-win_amd64.whl" for py in usable]
        if abi3:
            # Upstream builds a cp310-abi3 binary that runs on every Python >= 3.10:
            # compile once per CUDA, then retag it into one wheel per Python version.
            if force or not set(wheels) <= have:
                matrix.append({
                    "cu": cu, "cuda": cuda, "python": usable[-1], "archs": arch_list,
                    "retag": " ".join(cp(py) for py in usable), "wheels": " ".join(wheels),
                })
        else:
            for py, wheel in zip(usable, wheels):
                if force or wheel not in have:
                    matrix.append({
                        "cu": cu, "cuda": cuda, "python": py, "archs": arch_list,
                        "retag": "", "wheels": wheel,
                    })
    return {
        "version": version, "git_tag": git_tag, "post": post, "torch": torch_version,
        "cuda_variants": " ".join(sorted(variants, key=vkey)), "abi3": str(abi3).lower(), "tag": tag,
    }, matrix


def main():
    force = os.environ.get("FORCE", "false").lower() == "true"
    package = sys.argv[1] if len(sys.argv) > 1 else ""
    planners = {"talib": plan_talib, "sageattention": plan_sageattention}
    if package not in planners:
        sys.exit(f"usage: plan.py {'|'.join(planners)}")
    info, matrix = planners[package](force)

    outputs = dict(info, build=str(bool(matrix)).lower(), matrix=json.dumps({"include": matrix}))
    for k, v in outputs.items():
        print(f"{k}={v}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.writelines(f"{k}={v}\n" for k, v in outputs.items())
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### {package} {info['version']}\n\n")
            f.write("\n".join(f"- `{k}`: `{v}`" for k, v in info.items()) + "\n\n")
            f.write("**To build:**\n\n" + ("\n".join(f"- `{m.get('wheel') or m['wheels']}`" for m in matrix) or "- nothing, Release is up to date") + "\n")


if __name__ == "__main__":
    main()
