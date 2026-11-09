"""The native-splash patch of the python-for-android Qt bootstrap (scripts/build_android.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{{ args.package }}">
    <application android:label="@string/app_name" android:icon="@mipmap/icon">
        <activity android:name="org.kivy.android.PythonActivity"
                  android:label="@string/app_name"
                  android:theme="{{ args.android_apptheme }}"
                  android:configChanges="orientation|screenSize">
            <meta-data android:name="android.app.lib_name" android:value="main"/>
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
            </intent-filter>
        </activity>
    </application>
</manifest>
"""


@pytest.fixture(scope="module")
def build_android():
    if sys.version_info < (3, 11):
        pytest.skip("build script needs tomllib (Python 3.11+)")
    spec = importlib.util.spec_from_file_location("build_android", ROOT / "scripts" / "build_android.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_p4a(tmp_path: Path, template: str) -> Path:
    tdir = tmp_path / "pythonforandroid" / "bootstraps" / "qt" / "build" / "templates"
    tdir.mkdir(parents=True)
    (tdir / "AndroidManifest.tmpl.xml").write_text(template, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("theme", [True, False])
def test_splash_patch_adds_meta_data_theme_and_resources(build_android, tmp_path, theme):
    template = TEMPLATE if theme else TEMPLATE.replace('                  android:theme="{{ args.android_apptheme }}"\n', "")
    p4a = _fake_p4a(tmp_path, template)
    assert build_android.patch_splash(p4a)
    assert build_android.patch_splash(p4a)                 # idempotent
    text = (p4a / "pythonforandroid/bootstraps/qt/build/templates/AndroidManifest.tmpl.xml").read_text()
    assert text.count("android.app.splash_screen_drawable") == 1
    assert text.count('android:theme="@style/SplashTheme"') == 1
    activity = text[text.index("<activity"):text.index("</activity>")]
    assert "splash_screen_drawable" in activity            # inside the activity element
    res = p4a / "pythonforandroid/bootstraps/qt/build/src/main/res"
    assert (res / "drawable" / "splash.xml").read_text().count("@drawable/splash_logo") == 1
    assert "SplashTheme" in (res / "values" / "splash.xml").read_text()
    assert (res / "drawable-xhdpi" / "splash_logo.png").stat().st_size > 1000


def test_splash_patch_is_skipped_safely_without_template(build_android, tmp_path):
    assert build_android.patch_splash(tmp_path) is False
