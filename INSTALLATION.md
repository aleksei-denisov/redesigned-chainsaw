# Installation

## 1. Prerequisites

| Requirement | Version |
|---|---|
| Python | **3.10, 3.11, 3.12 or 3.13** (3.12 recommended) |
| Git | any recent version (or download the repository as a ZIP from GitHub) |
| Disk space | ~600 MB inside the virtual environment (PySide6 is large) |
| OS | macOS 12+ (Apple Silicon or Intel), Windows 10/11 64-bit |

Python packages (installed automatically from `requirements.txt`):
`numpy`, `scipy`, `PySide6`, `pyqtgraph`, `pytest`.

## 2. Install Python (if needed)

Check whether a suitable Python is already installed:

- macOS: `python3 --version`
- Windows (PowerShell): `py --version` (or `python --version`)

### macOS

Either

- download the **macOS 64-bit universal2 installer** from
  <https://www.python.org/downloads/macos/> (works on Apple Silicon and Intel) and run it, or
- with Homebrew: `brew install python@3.12`

### Windows

- Download the **Windows installer (64-bit)** from <https://www.python.org/downloads/windows/>.
- In the installer, tick **“Add python.exe to PATH”** and keep **“py launcher”** selected.
- Alternatively: `winget install Python.Python.3.12`

Open a *new* terminal afterwards so that the PATH change takes effect.

## 3. Get the code

```bash
git clone https://github.com/aleksei-denisov/redesigned-chainsaw.git
cd redesigned-chainsaw
```

Without Git: on GitHub click **Code → Download ZIP**, unzip it and open a terminal in the
extracted `redesigned-chainsaw` folder.

## 4. Create a virtual environment and install the dependencies

### macOS (Terminal, zsh/bash)

```bash
cd redesigned-chainsaw
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd redesigned-chainsaw
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell refuses to run `Activate.ps1` (“running scripts is disabled”), run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Windows (Command Prompt, cmd.exe)

```bat
cd redesigned-chainsaw
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If you only have one Python installed you can use `py -m venv .venv` (or `python -m venv .venv`).
When the environment is active, your prompt starts with `(.venv)`. Use `deactivate` to leave it.

## 5. Launch

With the virtual environment active:

```bash
python main.py
```

See [RUNNING.md](RUNNING.md) for everything you can do from there.

### One-step launchers (optional)

The repository contains launchers that create `.venv`, install the requirements on first use
and start the application:

- macOS / Linux: `./start.sh` (if it is not executable: `bash start.sh`)
- Windows: double-click `start.bat`, or run `start.bat` in a terminal

Arguments are passed through, e.g. `./start.sh --scenario triple_cart_lqr`.

### Optional: install as a command

```bash
python -m pip install .
pendulum-sim                # same as python main.py
```

## 6. Verify the installation

```bash
python -m pytest            # full test suite (~1 min), includes offscreen GUI tests
python main.py --smoke-test 2
```

The smoke test opens the window, runs the simulation for 2 s plus one experiment and exits
with code 0.

## 7. Standalone desktop application (optional)

You can bundle the application so it runs without a Python installation (build on the target
OS: a Windows build must be made on Windows, a macOS build on macOS).

```bash
python -m pip install pyinstaller
python scripts/build_app.py
```

Result:

- Windows: `dist\PendulumSimulator\PendulumSimulator.exe` (distribute the whole
  `dist\PendulumSimulator` folder)
- macOS: `dist/PendulumSimulator.app` (on Apple Silicon this build is arm64, on Intel x86_64;
  unsigned apps must be opened with right-click → **Open** the first time)

The CI workflow builds and smoke-tests both and keeps them as downloadable workflow artifacts.

## 8. Troubleshooting

| Problem | Solution |
|---|---|
| `python3: command not found` (macOS) / `'py' is not recognized` (Windows) | Install Python (section 2) and open a new terminal. On Windows try `python` instead of `py`. |
| `Missing Python packages: PySide6, …` when running `python main.py` | The virtual environment is not active or the install failed. Activate it (`source .venv/bin/activate` or `.venv\Scripts\Activate.ps1`) and rerun `python -m pip install -r requirements.txt`. |
| `ERROR: No matching distribution found for PySide6` | Your Python is too old/new or 32-bit. Use 64-bit Python 3.10–3.13. Check with `python -c "import sys, platform; print(sys.version, platform.machine())"`. |
| pip is very slow / times out | PySide6 is a ~200 MB download. Retry with `python -m pip install --default-timeout=120 -r requirements.txt`. Behind a proxy set `HTTPS_PROXY`. |
| macOS: `xcrun: error: invalid active developer path` while installing | Run `xcode-select --install` once (needed by some tools), then retry. |
| macOS: window does not appear / Python rocket icon only | Make sure you use a python.org or Homebrew Python (not the system Python 2/old CLT Python). |
| Windows: `qt.qpa.plugin: Could not load the Qt platform plugin "windows"` | Reinstall PySide6 inside the venv: `python -m pip install --force-reinstall PySide6`. Avoid paths with non-ASCII characters for the venv. |
| Windows: missing `VCRUNTIME140_1.dll` | Install the Microsoft Visual C++ Redistributable (x64) from Microsoft's website. |
| Linux: `Could not load the Qt platform plugin "xcb"` | Install the Qt runtime libraries, e.g. `sudo apt-get install libxcb-cursor0 libxkbcommon-x11-0 libegl1`. |
| Blurry UI on high-DPI displays (Windows) | Qt 6 is DPI-aware by default; check the Windows display scaling setting and restart the app. |
| The simulation runs slower than real time | The status bar shows “slower than real time”. Increase the physics time step (Simulation & view tab), reduce the speed factor, or hide some graphs. |
