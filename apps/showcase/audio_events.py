"""Bounded process-local wakeups and fresh audio receipts, never a command authority."""

import asyncio
import threading
import time


class SignalHub:
    def __init__(self, limit):
        self.limit = limit
        self.queues = set()
        self.loop = None

    def subscribe(self):
        if len(self.queues) >= self.limit:
            raise OverflowError("signal_capacity")
        self.loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        self.queues.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.queues.discard(queue)

    def notify(self):
        if self.loop is not None and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._deliver)
            except RuntimeError:
                pass

    def _deliver(self):
        for queue in tuple(self.queues):
            if queue.empty():
                queue.put_nowait(True)


class AudioReceipts:
    def __init__(self):
        self.lock = threading.Lock()
        self.probe_no = 0
        self.reports = {}

    def probe(self):
        with self.lock:
            self.probe_no += 1
            return self.probe_no

    def record(self, payload, active):
        with self.lock:
            now = time.monotonic()
            self.reports = {
                key: value for key, value in self.reports.items() if value[0] > now - 90
            }
            if not active:
                self.reports.pop(payload.display_id, None)
            elif payload.probe_no <= self.probe_no:
                if len(self.reports) >= 512 and payload.display_id not in self.reports:
                    self.reports.pop(min(self.reports, key=lambda k: self.reports[k][0]))
                self.reports[payload.display_id] = (
                    now,
                    payload.round_id,
                    payload.display_epoch,
                    payload.probe_no,
                    payload.command_id,
                    payload.status,
                )

    def fresh(self, display_id, round_id, epoch, probe_no):
        with self.lock:
            report = self.reports.get(display_id)
            return bool(
                report
                and report[1:3] == (round_id, epoch)
                and 0 < probe_no <= report[3] <= self.probe_no
            )

    def confirms(self, display_id, round_id, epoch, command_id, action):
        with self.lock:
            report = self.reports.get(display_id)
            return bool(
                report
                and report[1:3] == (round_id, epoch)
                and report[4] == command_id
                and (
                    report[5] in {"playing", "ended"}
                    if action == "play"
                    else report[5] != "playing"
                )
            )
