"""Build the Android APK (debug) or AAB (release) of the mobile interface.

Pipeline (the same steps run in GitHub Actions and in the Docker image)::

    stage sources  →  fetch official PySide6/shiboken6 Android wheels
                   →  pyside6-android-deploy --init   (generates p4a recipes + buildozer.spec)
                   →  patch buildozer.spec            (app id, name, icon, API levels, requirements)
                   →  buildozer android debug|release →  dist/*.apk | dist/*.aab

Requirements: Linux or macOS host, Python ≥ 3.10 with ``PySide6==<version>``,
``buildozer`` and ``cython`` installed (see android/requirements-build.txt), JDK 17,
Android SDK (platform + build-tools for ``--api``) and NDK r28c.

    python scripts/build_android.py --sdk-path $ANDROID_HOME --ndk-path $ANDROID_HOME/ndk/28.2.13676358
    python scripts/build_android.py ... --release      # signed AAB, see docs/ANDROID.md

The repository's desktop entry point (``main.py``) is not touched: the APK's
``main.py`` is ``android/main.py``, copied into a staging folder together with the
shared ``pendulum_sim`` package.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "android"
STAGING = BUILD / "app"
WHEELS = BUILD / "wheels"
DIST = ROOT / "dist"
ANDROID_DIR = ROOT / "android"

APP_TITLE = "Inverted Pendulum Simulator"
PACKAGE_DOMAIN = "com.adenisov"
PACKAGE_NAME = "invertedpendulum"          # → application id com.adenisov.invertedpendulum
MIN_API = 28                               # Qt 6.8+ minimum (Android 9)
TARGET_API = 36                            # Google Play requirement since 31 Aug 2026
# Python packages bundled besides PySide6/shiboken6. numpy is built by its
# python-for-android recipe; the others are pure Python. SciPy is deliberately
# absent: LQR seeding falls back to pendulum_sim/control/riccati.py.
REQUIREMENTS = ["numpy", "pyqtgraph==0.14.0", "colorama"]
WHEEL_INDEX = "https://download.qt.io/official_releases/QtForPython/{package}/"
# Not needed in the APK (desktop GUI internals stay: the mobile UI reuses ui/ widgets).
STAGE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")

log = logging.getLogger("build_android")


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    log.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def build_env() -> dict[str, str]:
    """Environment with this interpreter's bin directory first (buildozer finds cython etc.)."""
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = sys.prefix
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    return env


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


# --------------------------------------------------------------------- staging
def stage() -> None:
    """Copy the APK contents into build/android/app (keeping buildozer's cache)."""
    STAGING.mkdir(parents=True, exist_ok=True)
    for item in STAGING.iterdir():
        if item.name in (".buildozer",):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    shutil.copytree(ROOT / "pendulum_sim", STAGING / "pendulum_sim", ignore=STAGE_IGNORE)
    shutil.copytree(ROOT / "configs", STAGING / "configs")
    shutil.copy2(ANDROID_DIR / "main.py", STAGING / "main.py")
    shutil.copy2(ANDROID_DIR / "icon.png", STAGING / "icon.png")
    log.info("staged sources in %s", STAGING)


# ---------------------------------------------------------------------- wheels
def find_wheel(package: str, version: str, arch: str) -> Path:
    """Download (once) the official Qt for Python Android wheel for ``version``/``arch``.

    The file-name tags differ between releases (``cp311-cp311`` up to 6.11,
    ``cp310-abi3`` later, and the capitalisation of the project name changed), so
    the download index is searched instead of guessing the name.
    """
    WHEELS.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{package}-{re.escape(version)}-.*-android_{arch}\.whl$", re.IGNORECASE)
    cached = [p for p in WHEELS.iterdir() if pattern.match(p.name)]
    if cached:
        return cached[0]
    index_url = WHEEL_INDEX.format(package=package.lower())
    with urllib.request.urlopen(index_url, timeout=60) as response:
        html = response.read().decode("utf-8", "replace")
    names = sorted({m for m in re.findall(r'href="([^"?/]+\.whl)"', html) if pattern.match(m)})
    if not names:
        raise SystemExit(f"No {package} {version} Android wheel for {arch} at {index_url}. "
                         "Pick a version that has Android wheels (see docs/ANDROID.md).")
    target = WHEELS / names[-1]
    log.info("downloading %s", names[-1])
    urllib.request.urlretrieve(index_url + names[-1], target)
    return target


# --------------------------------------------------------- deployment config
def init_deployment(pyside_wheel: Path, shiboken_wheel: Path, sdk: Path, ndk: Path) -> None:
    tool = Path(sys.executable).parent / "pyside6-android-deploy"
    run([str(tool), "--init", "--force", "--verbose", "--keep-deployment-files", "--no-install",
         f"--name={PACKAGE_NAME}",
         f"--wheel-pyside={pyside_wheel}", f"--wheel-shiboken={shiboken_wheel}",
         f"--sdk-path={sdk}", f"--ndk-path={ndk}"],
        cwd=STAGING, env=build_env())


def patch_buildozer_spec(version: str, release: bool) -> dict[str, str]:
    spec_file = STAGING / "buildozer.spec"
    spec = configparser.ConfigParser(interpolation=None)
    spec.optionxform = str  # keep key case
    spec.read(spec_file, encoding="utf-8")
    app = spec["app"]
    reqs = [r for r in app.get("requirements", "").split(",") if r.strip()]
    for r in REQUIREMENTS:
        if r.split("==")[0] not in {x.split("==")[0] for x in reqs}:
            reqs.append(r)
    app["requirements"] = ",".join(reqs)
    app["title"] = APP_TITLE
    app["package.name"] = PACKAGE_NAME
    app["package.domain"] = PACKAGE_DOMAIN
    app["version"] = version
    app.pop("version.regex", None)
    app.pop("version.filename", None)
    app["icon.filename"] = "%(source.dir)s/icon.png"
    app["presplash.filename"] = "%(source.dir)s/icon.png"
    app["android.presplash_color"] = "#14213D"
    app["orientation"] = "portrait, landscape, portrait-reverse, landscape-reverse"
    app["fullscreen"] = "0"
    app["android.api"] = str(TARGET_API)
    app["android.minapi"] = str(MIN_API)
    app["android.ndk_api"] = str(MIN_API)
    app["android.archs"] = app.get("android.archs") or "arm64-v8a"
    app["android.accept_sdk_license"] = "True"
    app["android.skip_update"] = "True"
    app["android.allow_backup"] = "False"
    app["source.include_exts"] = ",".join(sorted(set(
        app.get("source.include_exts", "py").split(",") + ["py", "png", "json", "svg"])))
    app["source.exclude_dirs"] = "deployment,bin,.buildozer,tests"
    app["android.release_artifact"] = "aab" if release else "apk"
    app["android.debug_artifact"] = "apk"
    with spec_file.open("w", encoding="utf-8") as f:
        spec.write(f)
    summary = {k: app.get(k, "") for k in ("requirements", "android.api", "android.minapi",
                                           "android.archs", "p4a.commit", "p4a.extra_args")}
    for k, v in summary.items():
        log.info("buildozer %s = %s", k, v)
    return summary


# ------------------------------------------------------------------- building
def build(release: bool) -> list[Path]:
    env = build_env()
    if release:
        missing = [v for v in ("P4A_RELEASE_KEYSTORE", "P4A_RELEASE_KEYSTORE_PASSWD",
                               "P4A_RELEASE_KEYALIAS", "P4A_RELEASE_KEYALIAS_PASSWD") if not env.get(v)]
        if missing:
            raise SystemExit("Release builds need the signing variables: " + ", ".join(missing))
    run([sys.executable, "-m", "buildozer", "android", "release" if release else "debug"],
        cwd=STAGING, env=env)
    suffix = ".aab" if release else ".apk"
    found = sorted((p for p in STAGING.rglob(f"*{suffix}") if ".buildozer" not in p.parts),
                   key=lambda p: p.stat().st_mtime)
    if not found:
        found = sorted(STAGING.rglob(f"*{suffix}"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"buildozer finished but no {suffix} was found under {STAGING}")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{PACKAGE_NAME}-{project_version()}-{'release' if release else 'debug'}{suffix}"
    shutil.copy2(found[-1], out)
    log.info("built %s (%.1f MB)", out, out.stat().st_size / 1e6)
    return [out]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sdk-path", type=Path, default=os.environ.get("ANDROID_HOME"))
    parser.add_argument("--ndk-path", type=Path, default=os.environ.get("ANDROID_NDK_HOME"))
    parser.add_argument("--arch", choices=["aarch64", "x86_64"], default="aarch64",
                        help="aarch64 covers phones, tablets and Apple-Silicon emulators")
    parser.add_argument("--pyside-version", default=None,
                        help="defaults to the installed PySide6 (the deploy tool must match)")
    parser.add_argument("--release", action="store_true", help="signed AAB instead of a debug APK")
    parser.add_argument("--stage-only", action="store_true", help="only stage and fetch wheels")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    if sys.platform not in ("linux", "darwin"):
        raise SystemExit("pyside6-android-deploy only runs on Linux or macOS hosts")
    version = args.pyside_version
    if version is None:
        import PySide6

        version = PySide6.__version__
    stage()
    pyside = find_wheel("PySide6", version, args.arch)
    shiboken = find_wheel("shiboken6", version, args.arch)
    if args.stage_only:
        return 0
    if not args.sdk_path or not args.ndk_path:
        raise SystemExit("--sdk-path and --ndk-path are required (or ANDROID_HOME / ANDROID_NDK_HOME)")
    init_deployment(pyside, shiboken, args.sdk_path.resolve(), args.ndk_path.resolve())
    patch_buildozer_spec(project_version(), args.release)
    build(args.release)
    return 0


if __name__ == "__main__":
    sys.exit(main())
