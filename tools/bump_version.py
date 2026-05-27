import sys
import re
from pathlib import Path

def bump_version(part):
    root = Path(__file__).parent.parent
    pyproject_path = root / "pyproject.toml"
    
    if not pyproject_path.exists():
        print(f"Error: {pyproject_path} not found.")
        sys.exit(1)
        
    content = pyproject_path.read_text(encoding="utf-8")
    
    match = re.search(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        print("Error: Could not find version string in pyproject.toml")
        sys.exit(1)
        
    major, minor, patch = map(int, match.groups())
    old_version = f"{major}.{minor}.{patch}"
    
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    elif part == "patch":
        patch += 1
    else:
        print("Error: part must be major, minor, or patch")
        sys.exit(1)
        
    new_version = f"{major}.{minor}.{patch}"
    content = content.replace(f'version = "{old_version}"', f'version = "{new_version}"')
    
    pyproject_path.write_text(content, encoding="utf-8")
    
    # Also update __init__.py if it exists
    init_path = root / "libs" / "aja-core" / "aja" / "__init__.py"
    if init_path.exists():
        init_content = init_path.read_text(encoding="utf-8")
        if "__version__" in init_content:
            init_content = re.sub(r'__version__\s*=\s*".*"', f'__version__ = "{new_version}"', init_content)
        else:
            init_content += f'\n__version__ = "{new_version}"\n'
        init_path.write_text(init_content, encoding="utf-8")
    
    print(f"Bumped version: {old_version} -> {new_version}")

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ["major", "minor", "patch"]:
        print("Usage: python bump_version.py [major|minor|patch]")
        sys.exit(1)
    bump_version(sys.argv[1])
