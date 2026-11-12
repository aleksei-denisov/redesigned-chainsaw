#!/bin/bash
# Run by .github/workflows/android-emulator.yml inside the emulator runner.
set -x
mkdir -p out
adb install -r PendulumQuick-debug.apk 2>&1 | tee out/install.txt
adb shell getprop ro.product.cpu.abilist | tee out/abilist.txt

journal() {
  adb shell run-as "$PKG" sh -c 'find . -name start.log -o -name fault.log' > "out/journal-files-$1.txt" 2>&1
  for f in $(tr -d '\r' < "out/journal-files-$1.txt"); do
    echo "--- $f"; adb shell run-as "$PKG" cat "$f"
  done > "out/journal-$1.txt" 2>&1
}

run() {   # $1 = label, $2 = seconds
  adb logcat -c
  adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1
  for i in $(seq 1 "$2"); do
    sleep 1
    if [ $((i % 15)) -eq 0 ]; then
      adb exec-out screencap -p > "out/screen-$1-${i}s.png"
      echo "$1 ${i}s pid: $(adb shell pidof $PKG)"
    fi
  done
  adb logcat -d > "out/logcat-$1.txt"
  journal "$1"
  adb shell am force-stop "$PKG"
}

run first 120
run second 60
grep -nE "python|Python|Traceback|FATAL|AndroidRuntime|libc|DEBUG|qml|Qt" out/logcat-first.txt | tail -n 200 > out/logcat-first-filtered.txt
tail -n 80 out/logcat-first-filtered.txt
cat out/journal-first.txt out/journal-second.txt
exit 0
