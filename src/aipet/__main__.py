"""Entry point for AIPet.

Usage:
    python -m aipet                  # Launch production mode (launcher)
    python -m aipet gateway          # Launch Gateway only
    python -m aipet frontend         # Launch PyQt Frontend only
"""

import sys


def main() -> int:
    """Dispatch to the appropriate entry point."""
    if len(sys.argv) < 2:
        # Production mode: launcher
        from aipet.launcher import main as launcher_main
        return launcher_main()

    cmd = sys.argv[1].lower()
    if cmd == "gateway":
        from aipet.gateway.server import main as gateway_main
        return gateway_main()
    elif cmd in ("frontend", "gui", "client"):
        from aipet.frontend.app import main as frontend_main
        return frontend_main()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m aipet [gateway|frontend]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
