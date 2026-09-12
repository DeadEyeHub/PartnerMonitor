import concurrent.futures
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from partner_monitor.sources import load_sources
from partner_monitor.downloads import download

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {pool.submit(download, source, Path('data')): source['id'] for source in load_sources()}
        for task in concurrent.futures.as_completed(tasks):
            try:
                value = task.result()
                print(tasks[task], value['size'], value['origin'], flush=True)
            except Exception as exc:
                print(tasks[task], type(exc).__name__, flush=True)
