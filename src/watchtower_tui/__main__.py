"""Lets you run the app with `python -m watchtower`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
