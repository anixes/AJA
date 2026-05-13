import subprocess
import os

def run_health_check(file_path="src/prod/app.ts"):
    print(f"[-] Running Health Check on {file_path}...")
    try:
        # Try to run the app via tsx (using shell=True for Windows resolution)
        result = subprocess.run(
            f"npx tsx {file_path}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("[+] SYSTEM HEALTHY: No issues detected.")
            return True, None
        else:
            print("[!] SYSTEM FAILURE DETECTED!")
            print(f"Error Log: {result.stderr.strip()}")
            return False, result.stderr
            
    except Exception as e:
        print(f"[!] Health check crashed: {e}")
        return False, str(e)

def get_resource_telemetry() -> dict:
    """
    Jaarvis-inspired hardware awareness.
    Returns current CPU, RAM, and VRAM (if NVIDIA) metrics.
    """
    import shutil
    import os
    
    # CPU & RAM (using basic os/shutil to avoid psutil dependency for now)
    try:
        load = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0.0
    except Exception:
        load = 0.0
        
    total_m, used_m, free_m = shutil.disk_usage("/")
    
    telemetry = {
        "cpu_load": load,
        "disk_free_gb": round(free_m / (2**30), 2),
        "is_healthy": True
    }
    
    # Try VRAM (NVIDIA)
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=False
        )
        if res.returncode == 0:
            used, total = res.stdout.strip().split(",")
            telemetry["vram_used_mb"] = int(used)
            telemetry["vram_total_mb"] = int(total)
            telemetry["vram_available"] = int(total) - int(used)
    except Exception:
        pass
        
    return telemetry

if __name__ == "__main__":
    run_health_check()
