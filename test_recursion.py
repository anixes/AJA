import asyncio
import sys
import subprocess

async def run_cmd():
    proc = await asyncio.create_subprocess_shell(
        f'"{sys.executable}" -c "print(\'hello\')"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()

def test_multiple_runs():
    for i in range(5):
        print(f"Run {i}")
        asyncio.run(run_cmd())

test_multiple_runs()
