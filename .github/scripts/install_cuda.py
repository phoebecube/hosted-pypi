#!/usr/bin/env python3
"""
Minimal Windows CUDA toolkit for building PyTorch extensions.

Instead of the multi-GB installer, download only the redist components nvcc
needs (compiler, runtime, CCCL headers, and the CRT/NVVM split out in CUDA 13)
from NVIDIA's redist manifests, verify their sha256 and merge them into one
directory. ~100 MB instead of ~3 GB.

Usage:  install_cuda.py <X.Y.Z> <dest>
Exports CUDA_PATH / CUDA_HOME to GITHUB_ENV and <dest>\\bin to GITHUB_PATH.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REDIST = "https://developer.download.nvidia.com/compute/cuda/redist/"
PLATFORM = "windows-x86_64"
# cuda_crt / libnvvm only exist (and are only needed) from CUDA 13 on.
COMPONENTS = ["cuda_nvcc", "cuda_cudart", "cuda_cccl", "cuda_crt", "libnvvm"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hosted-pypi-cuda"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def install(component, entry, dest):
    data = fetch(REDIST + entry["relative_path"])
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise RuntimeError(f"{component}: sha256 mismatch ({digest} != {entry['sha256']})")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            # Strip the "<component>-windows-x86_64-<ver>-archive/" top-level directory.
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
            if not rel or info.is_dir():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return f"{component} {entry['relative_path'].rsplit('-', 2)[-2]} ({len(data) >> 20} MB)"


def main():
    version, dest = sys.argv[1], Path(sys.argv[2]).resolve()
    manifest = json.loads(fetch(f"{REDIST}redistrib_{version}.json"))
    jobs = {c: manifest[c][PLATFORM] for c in COMPONENTS if PLATFORM in manifest.get(c, {})}
    dest.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(len(jobs)) as pool:
        for line in pool.map(lambda c: install(c, jobs[c], dest), jobs):
            print("installed", line)

    if not (dest / "bin" / "nvcc.exe").exists() and os.name == "nt":
        sys.exit("nvcc.exe missing after install")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
            f.write(f"CUDA_PATH={dest}\nCUDA_HOME={dest}\n")
    if os.environ.get("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as f:
            f.write(f"{dest / 'bin'}\n")
    print(f"CUDA {version} -> {dest}")


if __name__ == "__main__":
    main()
