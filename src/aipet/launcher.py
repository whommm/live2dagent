"""Production launcher.

Automatically starts the Gateway (if not already running) and then the Frontend.
"""

import subprocess
import sys
import time


def _is_gateway_running(port: int = 18790) -> bool:
    """Check if the Gateway is already listening on the given port."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _start_gateway() -> subprocess.Popen[bytes]:
    """Start the Gateway as a subprocess."""
    python = sys.executable
    return subprocess.Popen(
        [python, "-m", "aipet", "gateway"],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _start_frontend() -> int:
    """Start the Frontend in the current process."""
    from aipet.frontend.app import main as frontend_main

    return frontend_main()


def main() -> int:
    """Launch live2dagent in production mode."""
    gateway_proc = None
    try:
        if not _is_gateway_running():
            print("[Launcher] Starting Gateway...")
            gateway_proc = _start_gateway()
            # Wait for Gateway to become ready
            for _ in range(30):
                time.sleep(0.5)
                if _is_gateway_running():
                    break
            else:
                print("[Launcher] Gateway failed to start in time.")
                if gateway_proc.poll() is not None:
                    stdout, stderr = gateway_proc.communicate()
                    print(stdout.decode(errors="replace"))
                    print(stderr.decode(errors="replace"), file=sys.stderr)
                return 1
            print("[Launcher] Gateway is ready.")
        else:
            print("[Launcher] Gateway already running.")

        print("[Launcher] Starting Frontend...")
        return _start_frontend()
    except KeyboardInterrupt:
        print("\n[Launcher] Interrupted by user.")
        return 0
    finally:
        if gateway_proc is not None and gateway_proc.poll() is None:
            print("[Launcher] Shutting down Gateway...")
            gateway_proc.terminate()
            try:
                gateway_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gateway_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
