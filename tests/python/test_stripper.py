from scripts.core.stripper import CommandStripper
import json

tests = [
    'DEBUG=true sudo timeout 10s npm install',
    'nice -n 19 python script.py',
    'env VAR=val stdbuf -oL /usr/bin/gcc main.c',
    'sudo chmod +x script.sh'
]

for cmd in tests:
    s = CommandStripper(cmd)
    s.strip()
    r = s.report()
    print(f"CMD: {cmd}")
    print(f"  ROOT: {r['Root Binary']}")
    print(f"  ENVS: {r['Env Vars']}")
    print(f"  WRAPPERS: {r['Wrappers']}")
    print("-" * 20)
