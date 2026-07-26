"""Entry point for the packaged binary.

Deliberately not `watchtower_tui/__main__.py`: PyInstaller runs its entry script as
a top-level module, so the relative `from .cli import main` in __main__.py
fails with "attempted relative import with no known parent package". An
absolute import from a separate launcher avoids the whole problem.
"""

import multiprocessing
import sys

from watchtower_tui.cli import main

if __name__ == "__main__":
    # Harmless from source, essential in a frozen build: without it a child
    # process would re-run the whole application instead of the worker.
    multiprocessing.freeze_support()
    sys.exit(main())
