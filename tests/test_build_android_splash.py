"""The native-splash patch of the python-for-android Qt bootstrap (scripts/build_android.py).

``tests/data/p4a_qt_*.tmpl.xml`` are copies of python-for-android's Qt bootstrap
templates (MIT licence, kivy/python-for-android); the activity's start tag contains
Jinja conditions with ``>`` in them, which broke a first, regex-on-the-tag version.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).with_name("data")


@pytest.fixture(scope="module")
def build_android():
    if sys.version_info < (3, 11):
        pytest.skip("build script needs tomllib (Python 3.11+)")
    spec = importlib.util.spec_from_file_location("build_android", ROOT / "scripts" / "build_android.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_p4a(tmp_path: Path) -> Path:
    tdir = tmp_path / "pythonforandroid" / "bootstraps" / "qt" / "build" / "templates"
    tdir.mkdir(parents=True)
    shutil.copy(DATA / "p4a_qt_AndroidManifest.tmpl.xml", tdir / "AndroidManifest.tmpl.xml")
    shutil.copy(DATA / "p4a_qt_strings.tmpl.xml", tdir / "strings.tmpl.xml")
    return tmp_path


def _render(path: Path) -> str:
    jinja2 = pytest.importorskip("jinja2")
    args = SimpleNamespace(
        numeric_version=1, version="1.0", min_sdk_version=28, permissions=[], wakelock=False,
        billing_pubkey=None, extra_manifest_xml="", allow_backup="false", backup_rules=None,
        extra_manifest_application_arguments="", android_apptheme="@android:style/Theme.NoTitleBar",
        window=True, android_used_libs=[], meta_data=[], android_entrypoint="org.kivy.android.PythonActivity",
        manifest_orientation="unspecified", activity_launch_mode="", intent_filters="", launcher=False,
        service_class_name="", package="com.example", add_activity=[], extra_manifest_application_xml="",
        display_cutout="never", name="Test", presplash_color="#000000")
    env = jinja2.Environment()
    return env.from_string(path.read_text(encoding="utf-8")).render(
        args=args, android_api=36, debug=True, service=False, service_data=[], native_services=[],
        private_version="1")


def test_splash_patch_on_the_real_templates(build_android, tmp_path):
    p4a = _fake_p4a(tmp_path)
    assert build_android.patch_splash(p4a)
    assert build_android.patch_splash(p4a)                 # idempotent
    tdir = p4a / "pythonforandroid/bootstraps/qt/build/templates"
    manifest = _render(tdir / "AndroidManifest.tmpl.xml")  # still valid Jinja
    strings = _render(tdir / "strings.tmpl.xml")
    activity = manifest[manifest.index("<activity"):manifest.index("</activity>")]
    assert activity.count("android.app.splash_screen_drawable") == 1
    assert '@drawable/splash"' in activity
    assert manifest.count("splash_screen_drawable") == 1
    style = strings[strings.index('<style name="KivySupportCutout">'):strings.index("</style>")]
    assert "android:windowBackground" in style and "@drawable/splash" in style
    res = p4a / "pythonforandroid/bootstraps/qt/build/src/main/res"
    assert (res / "drawable" / "splash.xml").read_text().count("@drawable/splash_logo") == 1
    assert "splash_background" in (res / "values" / "splash.xml").read_text()
    assert (res / "drawable-xhdpi" / "splash_logo.png").stat().st_size > 1000


def test_splash_patch_is_skipped_safely_without_template(build_android, tmp_path):
    assert build_android.patch_splash(tmp_path) is False
