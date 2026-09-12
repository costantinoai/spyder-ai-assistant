"""Read a subprocess's output under byte and wall-clock limits."""

import contextlib
import os
import signal
import subprocess
import threading
import time


def run_bounded_command(command, *, max_bytes, timeout, env=None):
    """Return (exit code, bytes, truncated), stopping excessive producers."""
    output = bytearray()
    truncated = threading.Event()
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=0, env=env,
        start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    def stop():
        with contextlib.suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()

    def read_output():
        try:
            while chunk := process.stdout.read(4096):
                remaining = max_bytes - len(output)
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated.set()
                    stop()
                    break
        except OSError:
            pass
        finally:
            process.stdout.close()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        # Descendants retaining stdout must not keep the reader alive forever.
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if reader.is_alive():
            stop()
            raise subprocess.TimeoutExpired(command, timeout)
    except BaseException:
        stop()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        reader.join(timeout=0.2)
        raise
    return process.returncode, bytes(output), truncated.is_set()
