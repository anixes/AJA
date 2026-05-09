import time
import subprocess
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path

class GraphUpdateHandler(FileSystemEventHandler):
    """
    Handles file change events to trigger local AST extraction.
    """
    def __init__(self):
        self.last_run = 0
        self.cooldown = 2  # Seconds to wait between runs to avoid thrashing

    def on_modified(self, event):
        if event.is_directory:
            return
        
        path = Path(event.src_path)
        if path.suffix in ['.ts', '.py', '.md'] and "graphify-out" not in str(path):
            current_time = time.time()
            if current_time - self.last_run > self.cooldown:
                print(f"File changed: {path.name} -> Updating Local Graph Structure...")
                self.run_local_map()
                self.last_run = current_time

    def run_local_map(self):
        try:
            # We run a specialized local-only extraction
            # This uses local CPU to map functions/classes (Zero Tokens)
            subprocess.run([
                "python", "scripts/local_extractor.py"
            ], capture_output=True, text=True)
            print("  - Local Map Updated. (0 tokens used)")
        except Exception as e:
            print(f"  - Error updating map: {e}")

if __name__ == "__main__":
    path = "."
    event_handler = GraphUpdateHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    
    print("Zero-Cost Graph Watcher Started.")
    print("Watching for changes in src/, scripts/, and docs/...")
    
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
