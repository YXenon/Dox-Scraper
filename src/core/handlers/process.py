import multiprocessing
from multiprocessing import Manager, Process, Pipe
import sys

if sys.platform == "linux":
    multiprocessing.set_start_method('fork')

# ---------------------------------------------------------------------------
# AppContext
# ---------------------------------------------------------------------------

class AppContext:
    """
    Shared application context for coordinating named child processes.

    Queues
    ------
    cmd_q  : commands sent to the main process (stop, restart).
    data_q : arbitrary data payloads between processes.
    ack_q   : acknowledgement / status signals.

    Lifecycle
    ---------
    1. Call `register(name, target)` once per process.
    2. Call `start(name)` to spawn it.
    3. Child processes can request stops/restarts via `request_stop` /
       `request_restart`; the main process drains those requests by calling
       `process_commands()` in a loop.
    """

    def __init__(self) -> None:
        self._manager = Manager()

        # Inter-process communication queues, and pipes (manager-backed, safe across PIDs).
        self.cmd_q  = self._manager.Queue()  # commands â main process
        self.scraper_conn, self.uploader_conn = Pipe() # duplex for scraper and uploader to contact
        self.scrape_q = self._manager.Queue() # for calling scraper with payloads

        # Keyed by process name.
        self.processes: dict[str, Process]                             = {}
        self.events:    dict[str, multiprocessing.managers.EventProxy] = {}  # type: ignore[name-defined]
        self.targets:   dict[str, callable]                            = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, target: callable) -> None:
        """Register a named process with its target function before starting it."""
        self.targets[name] = target
        self.events[name]  = self._manager.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, name: str) -> None:
        """
        Spawn the registered process.

        No-ops silently if the process is already alive, so callers do not need
        to guard against double-starts.
        """
        if name not in self.targets:
            raise ValueError(f"Process '{name}' is not registered.")

        if name in self.processes and self.processes[name].is_alive():
            return

        process = Process(target=self.targets[name], args=(self,))
        process.start()

        self.processes[name] = process
        self.events[name].set()

    def _stop(self, name: str) -> bool:
        """
        Terminate a running process and clear its event flag.

        Returns True if a process was found and stopped, False otherwise.
        """
        process = self.processes.pop(name, None)
        if process is None:
            return False

        process.terminate()
        process.join()
        self.events[name].clear()
        return True

    def _restart(self, name: str) -> None:
        """Stop then immediately re-spawn a named process."""
        self._stop(name)
        self.start(name)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_alive(self, name: str) -> bool:
        """Return True if the named process is currently running."""
        return self.events[name].is_set()

    def wait_for(self, name: str) -> None:
        """Block the caller until the named process has started."""
        self.events[name].wait()

    # ------------------------------------------------------------------
    # Remote control (called from child processes / threads)
    # ------------------------------------------------------------------

    def request_stop(self, name: str) -> None:
        """Ask the main process to stop the named process."""
        self.cmd_q.put({"cmd": "stop", "name": name})

    def request_restart(self, name: str) -> None:
        """Ask the main process to restart the named process."""
        self.cmd_q.put({"cmd": "restart", "name": name})

    # ------------------------------------------------------------------
    # Command loop (runs on the main process)
    # ------------------------------------------------------------------

    def process_commands(self) -> None:
        """
        Drain the command queue and act on each message.

        Intended to run on the main process in a dedicated thread or loop.
        Blocks indefinitely; child processes communicate via `request_stop` /
        `request_restart` rather than calling lifecycle methods directly.
        """
        while True:
            message = self.cmd_q.get()
            name    = message["name"]
            command = message["cmd"]

            if command == "stop":
                self._stop(name)
            elif command == "restart":
                self._restart(name)


# Module-level singleton shared across the application.
app_ctx = AppContext()
