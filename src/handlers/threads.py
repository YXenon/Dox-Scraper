import threading
import queue

class ThreadContext:
    def __init__(self, app_ctx):
        self.app_ctx = app_ctx          # access to main shared queues/events
        self.internal_q = queue.Queue()
        self.event = threading.Event()  # any inter-thread signaling
        self.threads = {}
        self.targets = {}

    def register(self, name, target):
        self.targets[name] = target

    def start(self, name):
        t = threading.Thread(target=self.targets[name], args=(self,), daemon=True)
        t.start()
        self.threads[name] = t

    def join_all(self):
        for t in self.threads.values():
            t.join()