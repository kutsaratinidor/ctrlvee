#!/usr/bin/env python3
"""First-time setup for CtrlVee.

Run with your system Python:
    macOS/Linux: python3 setup.py
    Windows:     python setup.py
"""
import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)
VENV_DIR = REPO_ROOT / ".venv"


def check_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        current = ".".join(str(part) for part in sys.version_info[:3])
        required = ".".join(str(part) for part in MIN_PYTHON)
        print(f"CtrlVee requires Python {required}+, but this is Python {current}.")
        print("Install a newer Python and re-run this script.")
        sys.exit(1)


def venv_python_path() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_activate_path() -> Path:
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "activate.bat"
    return VENV_DIR / "bin" / "activate"


def ensure_venv() -> Path:
    python_path = venv_python_path()
    activate_path = venv_activate_path()
    if python_path.exists() and activate_path.exists():
        print(f"Using existing virtual environment at {VENV_DIR}")
        return python_path

    if VENV_DIR.exists():
        # A previous run likely failed partway through (e.g. venv's internal
        # ensurepip step errored, which aborts before the activate scripts
        # are written) — python_path can exist without activate_path. Reusing
        # that half-built venv would leave activate missing forever, so start
        # clean instead.
        print(f"Found an incomplete virtual environment at {VENV_DIR}; removing it and starting over ...")
        shutil.rmtree(VENV_DIR)

    print(f"Creating virtual environment at {VENV_DIR} ...")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0:
        print("Failed to create the virtual environment.")
        print("On Debian/Ubuntu you may need: sudo apt install python3-venv python3-pip")
        sys.exit(1)
    return python_path


def ensure_pip(venv_python: Path) -> None:
    check = subprocess.run([str(venv_python), "-m", "pip", "--version"], capture_output=True)
    if check.returncode == 0:
        return

    # Some distro Pythons (notably Debian/Ubuntu without python3-pip installed)
    # create a venv with no pip bundled, even though `venv` creation itself
    # reports success. Bootstrap it explicitly before giving up.
    print("pip is missing from the virtual environment; bootstrapping with ensurepip ...")
    result = subprocess.run([str(venv_python), "-m", "ensurepip", "--upgrade"])
    if result.returncode != 0:
        print("Failed to bootstrap pip into the virtual environment.")
        print("On Debian/Ubuntu, install the full Python toolchain first:")
        print("  sudo apt install python3-venv python3-pip")
        print(f"then delete {VENV_DIR} and re-run this script.")
        sys.exit(1)


def install_dependencies(venv_python: Path) -> None:
    ensure_pip(venv_python)

    print("Upgrading pip ...")
    result = subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"])
    if result.returncode != 0:
        print("Failed to upgrade pip (see output above).")
        sys.exit(1)

    print("Installing dependencies from requirements.txt ...")
    result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(REPO_ROOT / "requirements.txt")]
    )
    if result.returncode != 0:
        print("Failed to install dependencies (see output above).")
        sys.exit(1)


def print_next_steps(venv_python: Path) -> None:
    if platform.system() == "Windows":
        activate = r".venv\Scripts\Activate.ps1"
    else:
        activate = "source .venv/bin/activate"

    print("\nSetup complete.")
    print("Before starting the bot, make sure VLC's Web interface is enabled")
    print("(VLC > Preferences > Interface > Main interfaces > check 'Web').")
    print("\nTo run CtrlVee:")
    print(f"  {activate}")
    print("  python bot.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="CtrlVee first-time setup")
    parser.add_argument(
        "--skip-venv",
        action="store_true",
        help="Skip venv creation and dependency install (development convenience)",
    )
    args = parser.parse_args()

    check_python_version()

    if not args.skip_venv:
        venv_python = ensure_venv()
        install_dependencies(venv_python)
    else:
        venv_python = venv_python_path()

    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        print(f"\n{env_path} already exists, skipping configuration.")
    else:
        from scripts.configure_env import run_wizard
        run_wizard(REPO_ROOT)

    print_next_steps(venv_python)


if __name__ == "__main__":
    main()
