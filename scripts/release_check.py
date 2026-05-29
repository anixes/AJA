import os
import sys
import argparse
import subprocess
import shutil
import venv
from pathlib import Path
from typing import List, Tuple

def print_header(title: str):
    print(f"\n{'='*50}\n{title}\n{'='*50}")

def print_step(msg: str):
    print(f"[>] {msg}")

def print_success(msg: str):
    print(f"[OK] {msg}")

def print_warn(msg: str):
    print(f"[WARNING] {msg}")

def print_error(msg: str):
    print(f"[ERROR] {msg}")

class ReleaseChecker:
    def __init__(self, strict: bool):
        self.strict = strict
        self.root_dir = Path(__file__).resolve().parent.parent
        self.venv_dir = self.root_dir / ".tmp_ci_env"
        self.venv_python = self.venv_dir / "Scripts" / "python.exe" if os.name == 'nt' else self.venv_dir / "bin" / "python"
        self.venv_pip = self.venv_dir / "Scripts" / "pip.exe" if os.name == 'nt' else self.venv_dir / "bin" / "pip"
        self.venv_aja = self.venv_dir / "Scripts" / "aja.exe" if os.name == 'nt' else self.venv_dir / "bin" / "aja"
        
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.blocked = False

    def report_warn(self, msg: str):
        print_warn(msg)
        self.warnings.append(msg)
        if self.strict:
            print_error(f"Strict mode enabled: elevating warning to BLOCKER.")
            self.errors.append(msg)
            self.blocked = True

    def report_error(self, msg: str):
        print_error(msg)
        self.errors.append(msg)
        self.blocked = True

    def run_cmd(self, cmd: List[str], cwd=None, env=None, check=True, capture=False) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, cwd=cwd or self.root_dir, env=env, check=check, capture_output=capture, text=True)
        except subprocess.CalledProcessError as e:
            if capture:
                print(e.stderr)
            if check:
                self.report_error(f"Command failed: {' '.join(cmd)}")
                sys.exit(1)
            return e

    def phase_contamination_audit(self):
        print_header("PHASE: Environment Contamination Audit")
        
        if "PYTHONPATH" in os.environ:
            self.report_warn("PYTHONPATH is set in the environment. This can shadow wheel installs.")
        else:
            print_success("PYTHONPATH is clean.")

        res = self.run_cmd([sys.executable, "-m", "pip", "show", "aja"], check=False, capture=True)
        if res.returncode == 0:
            self.report_warn("Package 'aja' is already installed in the outer environment. This might cause confusion.")
        else:
            print_success("No outer aja install detected.")

        res = self.run_cmd([sys.executable, "-m", "pip", "show", "aja-native"], check=False, capture=True)
        if res.returncode == 0:
            self.report_warn("Package 'aja-native' is already installed in the outer environment.")
        else:
            print_success("No outer aja-native install detected.")

    def phase_stale_artifact_detection(self):
        print_header("PHASE: Stale Artifact Detection")
        dist_dir = self.root_dir / "dist"
        if dist_dir.exists():
            wheels = list(dist_dir.glob("*.whl"))
            if wheels:
                self.report_warn(f"Found {len(wheels)} stale wheels in dist/. They will be deleted to ensure a clean build.")
                for w in wheels:
                    w.unlink()
        else:
            print_success("No stale wheels in dist/.")

        build_dir = self.root_dir / "build"
        if build_dir.exists():
            self.report_warn("Found build/ directory. Consider cleaning it to avoid cached CMake/Rust artifacts.")

        print_success("Artifact scan complete.")

    def phase_cleanroom_setup(self):
        print_header("PHASE: Cleanroom Setup")
        if self.venv_dir.exists():
            print_step("Removing existing venv...")
            shutil.rmtree(self.venv_dir)
        
        print_step("Creating fresh venv at .tmp_ci_env...")
        venv.create(self.venv_dir, with_pip=True, clear=True)
        
        # Upgrade pip and install maturin, pytest, anyio in venv
        print_step("Installing base CI dependencies...")
        self.run_cmd([str(self.venv_python), "-m", "pip", "install", "-U", "pip"])
        self.run_cmd([str(self.venv_pip), "install", "maturin", "pytest", "anyio", "psutil"])
        print_success("Cleanroom venv ready.")

    def phase_build_wheels(self):
        print_header("PHASE: Build Wheels")
        print_step("Building wheels via maturin...")
        # CI uses standard maturin build --release
        self.run_cmd([str(self.venv_python), "-m", "maturin", "build", "--release", "--out", "dist"])
        
        wheels = list((self.root_dir / "dist").glob("aja-*.whl"))
        if not wheels:
            self.report_error("No AJA wheels were produced in dist/!")
            sys.exit(1)
        print_success(f"Built wheel: {wheels[0].name}")

    def phase_clean_wheel_install(self):
        print_header("PHASE: Clean Wheel Install Test")
        
        # Ensure it's not installed
        self.run_cmd([str(self.venv_pip), "uninstall", "-y", "aja", "aja-native"], check=False)
        
        wheels = list((self.root_dir / "dist").glob("aja-*.whl"))
        wheel_path = [w for w in wheels if "aja_native" not in w.name][0]
        
        print_step(f"Installing wheel: {wheel_path.name}")
        self.run_cmd([str(self.venv_pip), "install", f"{str(wheel_path)}[all]"])
        
        # Verify import path
        print_step("Verifying import path does not shadow local directory...")
        
        # To avoid local shadowing, we run from a dummy temp directory inside the venv
        dummy_dir = self.venv_dir / "dummy"
        dummy_dir.mkdir(exist_ok=True)
        
        res = self.run_cmd(
            [str(self.venv_python), "-c", "import aja; print(aja.__file__)"], 
            cwd=dummy_dir,
            capture=True
        )
        
        imported_path = res.stdout.strip()
        print(f"Imported AJA from: {imported_path}")
        
        if "site-packages" not in imported_path.lower():
            self.report_error(f"Namespace leak! AJA imported from unexpected location: {imported_path}")
        else:
            print_success("AJA successfully imported from installed wheel.")

    def phase_validation(self):
        print_header("PHASE: Validation")
        
        print_step("Running aja doctor --ci ...")
        res = self.run_cmd([str(self.venv_aja), "doctor", "--ci"], check=False)
        if res.returncode != 0:
            self.report_error("aja doctor failed.")
            
        print_step("Running Pytest ...")
        res = self.run_cmd([str(self.venv_python), "-m", "pytest", "tests/python", "-x", "--tb=short"], check=False)
        if res.returncode != 0:
            self.report_error("Pytest failed.")
            
        if not self.blocked:
            print_success("Runtime validation passed.")

    def phase_docker_certification(self):
        print_header("PHASE: Docker Certification")
        
        # Check if docker is available
        res = self.run_cmd(["docker", "info"], check=False, capture=True)
        if res.returncode != 0:
            if self.strict:
                self.report_error("Docker is not running or not installed, but --strict mode is ON. BLOCKED.")
            else:
                print_warn("Docker is not running or not installed. Docker Certification: SKIPPED")
            return
            
        print_step("Building Docker image (--no-cache) ...")
        res = self.run_cmd(["docker", "build", "--no-cache", "-t", "aja:local-ci", "-f", "docker/Dockerfile", "."], check=False)
        if res.returncode != 0:
            self.report_error("Docker build failed.")
            return
            
        print_step("Running Docker smoke test ...")
        res = self.run_cmd(["docker", "run", "--rm", "aja:local-ci", "aja", "doctor"], check=False)
        if res.returncode != 0:
            self.report_error("Docker smoke test (aja doctor) failed.")
            return
            
        print_success("Docker certification passed.")

    def report(self):
        print_header("RELEASE GATING REPORT")
        
        print(f"Warnings: {len(self.warnings)}")
        print(f"Errors: {len(self.errors)}")
        print(f"Strict Mode: {'ON' if self.strict else 'OFF'}")
        
        if self.blocked:
            print("\n[ STATUS: BLOCKED ]")
            for e in self.errors:
                print(f"  - ERROR: {e}")
            sys.exit(1)
        else:
            print("\n[ STATUS: READY FOR PUSH ]")
            for w in self.warnings:
                print(f"  - WARN: {w}")

    def run_all(self):
        self.phase_contamination_audit()
        self.phase_stale_artifact_detection()
        self.phase_cleanroom_setup()
        self.phase_build_wheels()
        self.phase_clean_wheel_install()
        self.phase_validation()
        self.phase_docker_certification()
        self.report()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AJA Local CI Simulation & Release Certification")
    parser.add_argument("--strict", action="store_true", help="Enable strict mode (fails on warnings, requires Docker)")
    args = parser.parse_args()
    
    checker = ReleaseChecker(strict=args.strict)
    checker.run_all()
