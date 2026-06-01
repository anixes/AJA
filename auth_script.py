import os
import subprocess
from aja.copilot_auth import copilot_device_code_login

token = copilot_device_code_login()
if token:
    print(f"\n[AJA Auth] Setting COPILOT_GITHUB_TOKEN in user environment...")
    subprocess.run(["setx", "COPILOT_GITHUB_TOKEN", token], check=True)
    print("\n[AJA Auth] SUCCESS! Authentication completed and token saved persistently.")
    print("Please restart any open terminal windows to use AJA with Copilot.")
else:
    print("\n[AJA Auth] Failed to authenticate.")
