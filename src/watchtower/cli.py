"""Command line entry point.

`watchtower` with no arguments opens the dashboard, which is what almost everyone
wants. The subcommands exist for the cases where a TUI is the wrong shape:
checking where your data lives, listing accounts from a script, and wiping
everything before uninstalling.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .errors import WatchtowerError
from .logging_setup import configure, get_logger
from .paths import app_dir

log = get_logger("cli")


def _safe_print(text: str = "") -> None:
    """Print without exploding on a legacy Windows code page.

    The default console on Windows is often cp1252, which cannot encode the
    box drawing characters we use elsewhere. Losing a glyph is fine; a
    UnicodeEncodeError traceback is not.
    """
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watchtower",
        description="Track the usage limits on your Codex and Claude accounts.",
    )
    parser.add_argument("--version", action="version", version=f"Watchtower {__version__}")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Verbosity of the log file. Secrets are redacted at every level.",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Open the dashboard (the default).")
    sub.add_parser("where", help="Print the data directory and exit.")
    sub.add_parser("accounts", help="List configured accounts and exit.")
    sub.add_parser("doctor", help="Check the install and print what it found.")

    reset = sub.add_parser("reset", help="Delete all local Watchtower data.")
    reset.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    return parser


def cmd_where() -> int:
    directory = app_dir()
    _safe_print(str(directory))
    for name in (
        "config.json",
        "accounts.json",
        "usage-cache.json",
        "secrets.vault",
        "watchtower.log",
    ):
        path = directory / name
        _safe_print(f"  {'x' if path.exists() else ' '}  {name}")
    _safe_print()
    _safe_print("Tokens live in your OS keychain when there is one, otherwise in secrets.vault.")
    return 0


def cmd_doctor() -> int:
    """Check the things that break differently in a packaged build.

    A frozen single-file binary loses entry-point metadata and any data file
    nobody remembered to bundle, so the keychain quietly vanishing or the
    stylesheet going missing are the two realistic packaging failures. This
    reports both, plus enough environment detail to make a bug report useful.
    """
    import platform
    import sys as _sys

    ok = True
    frozen = getattr(_sys, "frozen", False)

    _safe_print(f"Watchtower {__version__}")
    _safe_print(f"  Python      {platform.python_version()} on {platform.platform()}")
    _safe_print(f"  Build       {'packaged binary' if frozen else 'from source'}")

    # -- data directory
    try:
        directory = app_dir()
        probe = directory / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _safe_print(f"  Data dir    {directory} (writable)")
    except Exception as exc:
        ok = False
        _safe_print(f"  Data dir    FAILED - {type(exc).__name__}")

    # -- secret storage
    try:
        from .secretstore import describe_backends

        for backend in describe_backends():
            mark = "available" if backend.available else "unavailable"
            _safe_print(f"  {backend.name:11} {mark} - {backend.detail}")
        if not any(b.available and b.key == "keyring" for b in describe_backends()):
            _safe_print("              (no keychain here, so the encrypted vault will be used)")
    except Exception as exc:
        ok = False
        _safe_print(f"  Secrets     FAILED - {type(exc).__name__}")

    # -- stylesheet, the classic thing to forget when packaging
    try:
        from .tui.app import WatchtowerApp

        css = WatchtowerApp.CSS_PATH
        if css.is_file() and css.stat().st_size > 0:
            _safe_print(f"  Stylesheet  found ({css.stat().st_size} bytes)")
        else:
            ok = False
            _safe_print(f"  Stylesheet  MISSING at {css}")
    except Exception as exc:
        ok = False
        _safe_print(f"  Stylesheet  FAILED - {type(exc).__name__}")

    # -- provider marks. The usual confusion is running one build while
    # editing another, so report what *this* binary can actually do.
    try:
        from .settings import LOGO_STYLES
        from .settings import load as _load_settings
        from .tui.widgets.logo import images_available, probe_image_support

        style = _load_settings().logo_style
        _safe_print(f"  Logo style  {style}  (available: {', '.join(LOGO_STYLES)})")
        if "image" not in LOGO_STYLES:
            _safe_print("              this build has no image support")
        elif not images_available():
            _safe_print("              image extra missing: pip install watchtower-tui[images]")
        elif probe_image_support():
            _safe_print("              terminal graphics: yes, icons will be drawn")
        else:
            _safe_print("              terminal graphics: no, falling back to dots")
    except Exception as exc:
        ok = False
        _safe_print(f"  Logo style  FAILED - {type(exc).__name__}")

    # -- providers
    try:
        from .providers import all_providers

        names = ", ".join(p.info.display_name for p in all_providers())
        _safe_print(f"  Providers   {names}")
    except Exception as exc:
        ok = False
        _safe_print(f"  Providers   FAILED - {type(exc).__name__}")

    _safe_print()
    _safe_print("All good." if ok else "Something above needs attention.")
    return 0 if ok else 1


def cmd_accounts() -> int:
    from .settings import load as load_settings
    from .store import AccountRepository

    settings = load_settings()
    accounts = AccountRepository().load()
    if not accounts:
        _safe_print("No accounts yet. Run `watchtower` and press a to add one.")
        return 0

    width = max(len(a.label) for a in accounts)
    for account in accounts:
        identity = account.identity or "-"
        if settings.mask_identities and "@" in identity:
            name, _, domain = identity.partition("@")
            identity = f"{name[:2]}****@{domain}"
        flags = "" if account.enabled else "  (paused)"
        _safe_print(
            f"{account.label.ljust(width)}  {account.provider:7}  "
            f"{account.plan or '-':10}  {identity}{flags}"
        )
    return 0


def cmd_reset(assume_yes: bool) -> int:
    """Remove local data. Deliberately spells out what is about to go."""
    import shutil

    from .secretstore import open_store
    from .settings import load as load_settings
    from .store import AccountRepository

    directory = app_dir()
    _safe_print(f"This deletes {directory} and every stored sign-in.")
    _safe_print("Your accounts with OpenAI and Anthropic are not affected.")

    if not assume_yes:
        try:
            answer = input("Type 'delete' to continue: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _safe_print("\nCancelled.")
            return 1
        if answer != "delete":
            _safe_print("Cancelled.")
            return 1

    # Clear keychain entries first: deleting the directory would orphan them.
    try:
        settings = load_settings()
        store = open_store(settings.secret_backend)
        if not store.locked:
            for account in AccountRepository().load():
                store.delete(f"account:{account.id}")
    except WatchtowerError as exc:
        _safe_print(f"Could not clean the keychain: {exc.friendly}")
    except Exception:
        log.exception("keychain cleanup failed during reset")
        _safe_print("Could not clean the keychain; check it manually for 'Watchtower' entries.")

    try:
        shutil.rmtree(directory)
    except OSError as exc:
        _safe_print(f"Could not delete {directory}: {exc.strerror or exc}")
        return 1

    _safe_print("Done.")
    return 0


def cmd_run() -> int:
    from .service import AccountManager
    from .settings import load as load_settings
    from .tui import WatchtowerApp

    settings = load_settings()
    try:
        manager = AccountManager(settings)
    except WatchtowerError as exc:
        _safe_print(f"Watchtower could not start: {exc.friendly}")
        return 1

    WatchtowerApp(settings, manager).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        configure(level=args.log_level)
    except Exception:
        # Logging must never be the reason the app will not start.
        pass

    command = args.command or "run"
    try:
        if command == "where":
            return cmd_where()
        if command == "doctor":
            return cmd_doctor()
        if command == "accounts":
            return cmd_accounts()
        if command == "reset":
            return cmd_reset(args.yes)
        return cmd_run()
    except KeyboardInterrupt:
        return 130
    except WatchtowerError as exc:
        _safe_print(f"watchtower: {exc.friendly}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
