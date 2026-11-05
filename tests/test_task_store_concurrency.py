"""Concurrent task status updates expose complete snapshots to readers."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from archon_horizon.core.tasks import HorizonTask
from archon_horizon.store.filesystem import FilesystemTaskStore


def test_task_readers_never_observe_partial_concurrent_writes(tmp_path: Path) -> None:
    store = FilesystemTaskStore(tmp_path / "tasks")
    store.put(HorizonTask(id="T-1", project="p", objective="initial"))
    barrier = threading.Barrier(3)

    def write(size: int) -> None:
        barrier.wait(timeout=5)
        for number in range(50):
            store.put(HorizonTask(id="T-1", project="p", objective=str(number) * size))

    def read() -> None:
        barrier.wait(timeout=5)
        for _ in range(200):
            assert store.get("T-1").id == "T-1"

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(write, 100), executor.submit(write, 10000), executor.submit(read)]
        for future in futures:
            future.result(timeout=10)
    assert not list((tmp_path / "tasks" / "items").glob(".task-*.tmp"))
