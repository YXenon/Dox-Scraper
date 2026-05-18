import multiprocessing
import time
from multiprocessing import Process


class AppContext:
    def __init__(self):
        self._manager = multiprocessing.Manager()
        self.cmd_q = self._manager.Queue() # meant for commands sent to main process
        self.data_q = self._manager.Queue() # meant for sending data
        self.ok_q = self._manager.Queue() # meant for sending ok signals
        self.processes = {}
        self.events = {}
        self.targets = {}

    def register(self, name, target):
        """Register a process with its target function."""
        self.targets[name] = target
        self.events[name] = self._manager.Event()

    def start(self, name):
        '''Start a registered process.'''
        if name not in self.targets:
            raise ValueError(f"Process '{name}' not registered.")
        if name in self.processes and self.processes[name].is_alive():
            return
        p = Process(target=self.targets[name], args=(self,))
        p.start()
        self.processes[name] = p
        self.events[name].set()

    def _stop(self, name):
        p = self.processes.pop(name, None)
        if p:
            p.terminate()
            p.join()
            self.events[name].clear()
            return True
        return False

    def _restart(self, name):
        self._stop(name)
        self.start(name)

    def is_alive(self, name):
        '''Check whether a process is running or not'''
        return self.events[name].is_set()

    def wait_for(self, name):
        """Block until named process is running."""
        self.events[name].wait()

    def request_stop(self, name):
        self.cmd_q.put({"cmd": "stop", "name": name})

    def request_restart(self, name):
        self.cmd_q.put({"cmd": "restart", "name": name})

    def process_commands(self):
        """Wait for commands from other processes/threads to send commands to main, so it can take actions accordingly."""
        while True:
            msg = self.cmd_q.get()
            name = msg["name"]
            if msg["cmd"] == "stop":
                self._stop(name)
            elif msg["cmd"] == "restart":
                self._restart(name)

app_ctx = AppContext()