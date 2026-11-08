# Linux build environment for the Android APK (used on macOS/Windows hosts).
#
#   docker build --platform linux/amd64 -t pendulum-android -f android/Dockerfile .
#   docker run --rm --platform linux/amd64 -v "$PWD":/src -v pendulum-android-cache:/root/.buildozer \
#       pendulum-android                      # → dist/invertedpendulum-<version>-debug.apk
#
# The Android NDK's Linux toolchain is x86_64-only, hence linux/amd64. On Apple Silicon
# enable "Use Rosetta for x86_64/amd64 emulation" in Docker Desktop for usable speed.
FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_NDK_VERSION=28.2.13676358 \
    ANDROID_API_LEVEL=36 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless python3 python3-venv python3-pip git curl unzip zip \
        autoconf automake libtool libltdl-dev pkg-config cmake ninja-build \
        libffi-dev libssl-dev zlib1g-dev gfortran build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Android command-line tools, platform, build-tools and NDK r28c
RUN mkdir -p $ANDROID_HOME/cmdline-tools && cd /tmp \
    && curl -fsSL -o tools.zip https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip \
    && unzip -q tools.zip && mv cmdline-tools $ANDROID_HOME/cmdline-tools/latest && rm tools.zip \
    && yes | $ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager --licenses > /dev/null \
    && $ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager "platform-tools" \
         "platforms;android-$ANDROID_API_LEVEL" "build-tools;$ANDROID_API_LEVEL.0.0" "ndk;$ANDROID_NDK_VERSION" \
    && ln -s $ANDROID_HOME/cmdline-tools/latest $ANDROID_HOME/tools

COPY android/requirements-build.txt /tmp/requirements-build.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r /tmp/requirements-build.txt

WORKDIR /src
CMD ["/bin/sh", "-c", "/opt/venv/bin/python scripts/build_android.py --sdk-path $ANDROID_HOME --ndk-path $ANDROID_HOME/ndk/$ANDROID_NDK_VERSION \"$@\"", "--"]
