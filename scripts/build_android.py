"""Build the Android APK (debug) or AAB (release) of the mobile interface.

Pipeline (the same steps run in GitHub Actions and in the Docker image)::

    stage sources  →  fetch official PySide6/shiboken6 Android wheels
                   →  pyside6-android-deploy --init   (generates p4a recipes + buildozer.spec)
                   →  patch buildozer.spec            (app id, name, icon, API levels, requirements)
                   →  python-for-android checkout     (pinned commit + native splash screen patch)
                   →  buildozer android debug|release →  dist/*.apk | dist/*.aab

Requirements: Linux or macOS host, Python ≥ 3.10 with ``PySide6==<version>``,
``buildozer`` and ``cython`` installed (see android/requirements-build.txt), JDK 17,
Android SDK (platform + build-tools for ``--api``) and NDK r28c.

    python scripts/build_android.py --sdk-path $ANDROID_HOME --ndk-path $ANDROID_HOME/ndk/28.2.13676358
    python scripts/build_android.py ... --release      # signed AAB, see docs/ANDROID.md
    python scripts/build_android.py ... --ui quick     # the Qt Quick interface (own app id)

The repository's desktop entry point (``main.py``) is not touched: the APK's
``main.py`` is ``android/main.py``, copied into a staging folder together with the
shared ``pendulum_sim`` package.

Two build targets (``--ui``) produce two apps that install side by side:

* ``widgets`` (default) – the Qt Widgets mobile interface, ``com.adenisov.invertedpendulum``;
* ``quick`` – the Qt Quick interface (``pendulum_sim/quick``), ``com.adenisov.invertedpendulumquick``,
  staged without the Widgets UI packages so that only the Qt Quick libraries are loaded.
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
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "android"
STAGING = BUILD / "app"
WHEELS = BUILD / "wheels"
DIST = ROOT / "dist"
ANDROID_DIR = ROOT / "android"
P4A_DIR = BUILD / "python-for-android"
P4A_URL = "https://github.com/kivy/python-for-android.git"
SPLASH_BG = "#15181C"                      # = pendulum_sim/mobile/theme.py BG (seamless hand-over)

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
INCLUDE_EXTS = ["py", "png", "json", "svg"]


class Target(NamedTuple):
    """One Android app built from this repository."""

    key: str
    title: str
    package_name: str                 # application id = PACKAGE_DOMAIN + "." + package_name
    staging: Path                     # buildozer project directory (keeps its own build cache)
    entry: str                        # file in android/ that becomes the APK's main.py
    requirements: tuple[str, ...]
    include_exts: tuple[str, ...]
    # parts of pendulum_sim to stage: None = the whole package except ``exclude``;
    # otherwise package names, or "package/module.py" for single modules
    parts: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    artifact: str = ""                # dist/<artifact>-<version>-debug.apk


TARGETS = {
    "widgets": Target(
        key="widgets", title=APP_TITLE, package_name=PACKAGE_NAME, staging=STAGING, entry="main.py",
        requirements=tuple(REQUIREMENTS), include_exts=tuple(INCLUDE_EXTS),
        exclude=("quick",),           # its QML files would pull the Qt Quick libraries into this app
        artifact=PACKAGE_NAME),
    "quick": Target(
        key="quick", title="Pendulum Quick", package_name="invertedpendulumquick",
        staging=BUILD / "app-quick", entry="main_quick.py",
        requirements=("numpy",), include_exts=(*INCLUDE_EXTS, "qml", "ttf", "txt"),
        # the shared core, the Qt Quick UI and the Qt-free helpers it reuses from the mobile package
        parts=("__init__.py", "physics", "control", "simulation", "quick",
               "mobile/__init__.py", "mobile/plot_math.py", "mobile/startup.py", "mobile/cpu.py"),
        artifact="invertedpendulumquick"),
}

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
def stage(target: Target = TARGETS["widgets"], staging: Path | None = None) -> Path:
    """Copy the APK contents into the target's staging folder (keeping buildozer's cache)."""
    staging = staging or target.staging
    staging.mkdir(parents=True, exist_ok=True)
    for item in staging.iterdir():
        if item.name in (".buildozer",):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    source, package = ROOT / "pendulum_sim", staging / "pendulum_sim"
    if target.parts is None:
        excluded = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", *target.exclude)
        shutil.copytree(source, package, ignore=excluded)
    else:
        for part in target.parts:
            if (source / part).is_dir():
                shutil.copytree(source / part, package / part, ignore=STAGE_IGNORE)
            else:
                (package / part).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / part, package / part)
    shutil.copytree(ROOT / "configs", staging / "configs")
    shutil.copy2(ANDROID_DIR / target.entry, staging / "main.py")
    shutil.copy2(ANDROID_DIR / "icon.png", staging / "icon.png")
    log.info("staged sources of the '%s' target in %s", target.key, staging)
    return staging


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
def init_deployment(pyside_wheel: Path, shiboken_wheel: Path, sdk: Path, ndk: Path,
                    target: Target = TARGETS["widgets"]) -> None:
    tool = Path(sys.executable).parent / "pyside6-android-deploy"
    run([str(tool), "--init", "--force", "--verbose", "--keep-deployment-files", "--no-install",
         f"--name={target.package_name}",
         f"--wheel-pyside={pyside_wheel}", f"--wheel-shiboken={shiboken_wheel}",
         f"--sdk-path={sdk}", f"--ndk-path={ndk}"],
        cwd=target.staging, env=build_env())


def patch_buildozer_spec(version: str, release: bool, target: Target = TARGETS["widgets"]) -> dict[str, str]:
    spec_file = target.staging / "buildozer.spec"
    spec = configparser.ConfigParser(interpolation=None)
    spec.optionxform = str  # keep key case
    spec.read(spec_file, encoding="utf-8")
    app = spec["app"]
    reqs = [r for r in app.get("requirements", "").split(",") if r.strip()]
    for r in target.requirements:
        if r.split("==")[0] not in {x.split("==")[0] for x in reqs}:
            reqs.append(r)
    app["requirements"] = ",".join(reqs)
    app["title"] = target.title
    app["package.name"] = target.package_name
    app["package.domain"] = PACKAGE_DOMAIN
    app["version"] = version
    app.pop("version.regex", None)
    app.pop("version.filename", None)
    app["icon.filename"] = "%(source.dir)s/icon.png"
    app["presplash.filename"] = "%(source.dir)s/icon.png"
    app["android.presplash_color"] = SPLASH_BG
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
        app.get("source.include_exts", "py").split(",") + list(target.include_exts))))
    app["source.exclude_dirs"] = "deployment,bin,.buildozer,tests"
    app["android.release_artifact"] = "aab" if release else "apk"
    app["android.debug_artifact"] = "apk"
    p4a = prepare_p4a(app.get("p4a.branch") or "master", app.get("p4a.commit", ""))
    if p4a is not None:
        app["p4a.source_dir"] = str(p4a)
    with spec_file.open("w", encoding="utf-8") as f:
        spec.write(f)
    summary = {k: app.get(k, "") for k in ("requirements", "android.api", "android.minapi",
                                           "android.archs", "p4a.commit", "p4a.source_dir", "p4a.extra_args")}
    for k, v in summary.items():
        log.info("buildozer %s = %s", k, v)
    return summary


# --------------------------------------------------------------- splash screen
SPLASH_DRAWABLE = """<?xml version="1.0" encoding="utf-8"?>
<!-- Native splash: logo and name centred on the app background (no scaling of the bitmap). -->
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item android:drawable="@color/splash_background"/>
    <item><bitmap android:gravity="center" android:src="@drawable/splash_logo"/></item>
</layer-list>
"""
SPLASH_VALUES = f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="splash_background">{SPLASH_BG}</color>
</resources>
"""
SPLASH_META = ('<meta-data android:name="android.app.splash_screen_drawable" '
               'android:resource="@drawable/splash"/>')
# window background until Qt draws its first frame (also behind the Android 12+ system splash)
SPLASH_WINDOW_ITEM = '<item name="android:windowBackground">@drawable/splash</item>'


def prepare_p4a(branch: str, commit: str) -> Path | None:
    """Check out python-for-android at the commit pyside6-android-deploy pinned, add the splash.

    The Qt bootstrap's activity (Qt's QtActivity) shows the drawable named by the
    ``android.app.splash_screen_drawable`` meta-data from launch until Qt draws its
    first frame; the ``presplash`` options of python-for-android only apply to the SDL2
    bootstrap. The patch is defensive: if the template has changed, the build goes on
    without the native splash (the in-app loading screen still appears).
    """
    try:
        if not (P4A_DIR / ".git").exists():
            run(["git", "clone", "--filter=blob:none", "-b", branch, P4A_URL, str(P4A_DIR)], cwd=BUILD)
        if commit:
            run(["git", "-c", "advice.detachedHead=false", "checkout", "-f", commit], cwd=P4A_DIR)
        else:
            run(["git", "checkout", "-f", branch], cwd=P4A_DIR)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("could not prepare python-for-android (%s); buildozer clones it, no native splash", exc)
        return None
    patch_splash(P4A_DIR)
    patch_back_key(P4A_DIR)
    patch_activity_callbacks(P4A_DIR)
    return P4A_DIR


BACK_KEY_MARK = "Inverted Pendulum Simulator: keys go to Qt"
BACK_KEY_LINE = (f"        // {BACK_KEY_MARK} (the app handles Back: previous screen, exit dialog)\n"
                 "        if (true) return super.onKeyDown(keyCode, event);\n")


def patch_back_key(p4a: Path) -> bool:
    """Let Qt receive every key press, Back included (idempotent).

    The Qt bootstrap's ``PythonActivity.onKeyDown`` swallows a key press – any key at the
    pinned commit – with a "Click again to close the app" toast unless the previous one
    was less than 2 s ago, so the app's own Back handling (previous screen, then an exit
    dialog) only ran on every second press. A first statement returning
    ``super.onKeyDown`` (Qt's activity, which forwards the key to the app) restores the
    normal behaviour; the original body stays below it (``if (true)`` keeps it compilable).
    """
    java = (p4a / "pythonforandroid" / "bootstraps" / "qt" / "build" / "src" / "main" / "java" / "org"
            / "kivy" / "android" / "PythonActivity.java")
    if not java.exists():
        log.warning("no Qt bootstrap activity at %s: Back key not patched", java)
        return False
    text = java.read_text(encoding="utf-8")
    if BACK_KEY_MARK in text:
        return True
    head = re.search(r"public boolean onKeyDown\(int keyCode, KeyEvent event\)\s*\{\n", text)
    if head is None:
        log.warning("onKeyDown not found in %s: Back key not patched", java)
        return False
    java.write_text(text[:head.end()] + BACK_KEY_LINE + text[head.end():], encoding="utf-8")
    log.info("Back key handed to Qt in %s", java)
    return True


def patch_splash(p4a: Path) -> bool:
    """Add the splash to the Qt bootstrap's templates and resources (idempotent).

    * ``AndroidManifest.tmpl.xml``: the meta-data goes into the app's activity, right
      before its ``android.app.lib_name`` meta-data (the activity's start tag holds Jinja
      conditions with ``>`` in them, so it is not edited);
    * ``strings.tmpl.xml``: the activity's style (``KivySupportCutout``) gets the splash
      as window background;
    * ``res/``: the layer-list drawable, the logo bitmap and the background colour.
    """
    bootstrap = p4a / "pythonforandroid" / "bootstraps" / "qt" / "build"
    manifest = bootstrap / "templates" / "AndroidManifest.tmpl.xml"
    strings = bootstrap / "templates" / "strings.tmpl.xml"
    if not manifest.exists():
        log.warning("no Qt bootstrap manifest template at %s: no native splash", manifest)
        return False
    text = manifest.read_text(encoding="utf-8")
    if "splash_screen_drawable" not in text:
        anchor = re.search(r'[ \t]*<meta-data\s+android:name="android\.app\.lib_name"', text)
        if anchor is None:
            log.warning("activity meta-data not found in %s: no native splash", manifest)
            return False
        indent = re.match(r"[ \t]*", anchor.group(0)).group(0)
        text = text[:anchor.start()] + indent + SPLASH_META + "\n" + text[anchor.start():]
        manifest.write_text(text, encoding="utf-8")
    if strings.exists():
        st = strings.read_text(encoding="utf-8")
        if "android:windowBackground" not in st:
            style = re.search(r'([ \t]*)<style name="KivySupportCutout"[^>]*>\n', st)
            if style is not None:
                st = st[:style.end()] + style.group(1) + "    " + SPLASH_WINDOW_ITEM + "\n" + st[style.end():]
                strings.write_text(st, encoding="utf-8")
            else:
                log.warning("activity style not found in %s: no splash window background", strings)
    res = bootstrap / "src" / "main" / "res"
    for sub in ("drawable", "drawable-xhdpi", "values"):
        (res / sub).mkdir(parents=True, exist_ok=True)
    (res / "drawable" / "splash.xml").write_text(SPLASH_DRAWABLE, encoding="utf-8")
    (res / "values" / "splash.xml").write_text(SPLASH_VALUES, encoding="utf-8")
    shutil.copy2(ANDROID_DIR / "splash_logo.png", res / "drawable-xhdpi" / "splash_logo.png")
    log.info("native splash added to the Qt bootstrap (%s)", manifest)
    return True


# ------------------------------------------------------------------- building
def build(release: bool, target: Target = TARGETS["widgets"]) -> list[Path]:
    env = build_env()
    staging = target.staging
    if release:
        missing = [v for v in ("P4A_RELEASE_KEYSTORE", "P4A_RELEASE_KEYSTORE_PASSWD",
                               "P4A_RELEASE_KEYALIAS", "P4A_RELEASE_KEYALIAS_PASSWD") if not env.get(v)]
        if missing:
            raise SystemExit("Release builds need the signing variables: " + ", ".join(missing))
    run([sys.executable, "-m", "buildozer", "android", "release" if release else "debug"],
        cwd=staging, env=env)
    suffix = ".aab" if release else ".apk"
    found = sorted((p for p in staging.rglob(f"*{suffix}") if ".buildozer" not in p.parts),
                   key=lambda p: p.stat().st_mtime)
    if not found:
        found = sorted(staging.rglob(f"*{suffix}"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"buildozer finished but no {suffix} was found under {staging}")
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{target.artifact}-{project_version()}-{'release' if release else 'debug'}{suffix}"
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
    parser.add_argument("--ui", choices=sorted(TARGETS), default="widgets",
                        help="which app to build: the Qt Widgets interface (default) or the Qt Quick one")
    args = parser.parse_args()
    target = TARGETS[args.ui]
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    if sys.platform not in ("linux", "darwin"):
        raise SystemExit("pyside6-android-deploy only runs on Linux or macOS hosts")
    version = args.pyside_version
    if version is None:
        import PySide6

        version = PySide6.__version__
    stage(target)
    pyside = find_wheel("PySide6", version, args.arch)
    shiboken = find_wheel("shiboken6", version, args.arch)
    if args.stage_only:
        return 0
    if not args.sdk_path or not args.ndk_path:
        raise SystemExit("--sdk-path and --ndk-path are required (or ANDROID_HOME / ANDROID_NDK_HOME)")
    init_deployment(pyside, shiboken, args.sdk_path.resolve(), args.ndk_path.resolve(), target)
    patch_buildozer_spec(project_version(), args.release, target)
    build(args.release, target)
    return 0


CALLBACK_MARK = "Inverted Pendulum Simulator: Qt gets the result too"
CALLBACKS = {  # method header → call of the overridden method (QtActivity's)
    "protected void onActivityResult(int requestCode, int resultCode, Intent intent) {":
        "super.onActivityResult(requestCode, resultCode, intent);",
    "protected void onNewIntent(Intent intent) {": "super.onNewIntent(intent);",
}


def patch_activity_callbacks(p4a: Path) -> bool:
    """Pass activity results and new intents on to Qt's activity (idempotent).

    The Qt bootstrap's ``PythonActivity`` overrides ``onActivityResult`` (and
    ``onNewIntent``) for python-for-android listeners without calling ``super``. Qt's file
    dialogs on Android are the system's storage picker, whose answer arrives through
    ``onActivityResult`` – Qt never got it, so every *Save … / Open …* left a modal
    dialog waiting forever: the app still drew but no button reacted after returning.
    """
    java = (p4a / "pythonforandroid" / "bootstraps" / "qt" / "build" / "src" / "main" / "java" / "org"
            / "kivy" / "android" / "PythonActivity.java")
    if not java.exists():
        log.warning("no Qt bootstrap activity at %s: activity callbacks not patched", java)
        return False
    text = java.read_text(encoding="utf-8")
    if CALLBACK_MARK in text:
        return True
    for header, call in CALLBACKS.items():
        i = text.find(header)
        if i < 0:
            log.warning("%s not found in %s", header, java)
            return False
        end = i + len(header)
        text = text[:end] + f"\n        {call}  // {CALLBACK_MARK}" + text[end:]
    java.write_text(text, encoding="utf-8")
    log.info("activity results handed to Qt in %s", java)
    return True


if __name__ == "__main__":
    sys.exit(main())
