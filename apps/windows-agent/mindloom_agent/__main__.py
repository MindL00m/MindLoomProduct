"""``python -m mindloom_agent``: sign in, wait for automations, do their Windows steps.

``python -m mindloom_agent --driver-selftest <LoomFixture.exe> <log file>`` checks the
driver against the fixture app instead (see ``scripts/driver-selftest.ps1``).
"""

from __future__ import annotations

import logging
import sys
import threading

from . import __version__, config
from .api import ApiClient
from .auth import SignInError, SignInSession
from .runner import PENDING_POLL_SECONDS, Runner

logger = logging.getLogger("mindloom_agent")


class AgentApp:
    def __init__(self) -> None:
        from .ui import OverlayWindow

        self.config = config.load()
        self.quit = threading.Event()
        self.runner: Runner | None = None
        self._sign_in_session: SignInSession | None = None
        self.window = OverlayWindow(on_stop=self._stop, on_sign_in=self._sign_in,
                                    on_sign_out=self._sign_out, on_quit=self._quit)
        self._show_account()

    def _show_account(self, status: str | None = None) -> None:
        signed_in = self.config.signed_in
        self.window.set_account(
            (self.config.email or "your MindLoom account") if signed_in else None,
            status if status is not None else (
                "Waiting for automations. Keep this open; MindLoom shows a small window when it starts working."
                if signed_in else f"Sign in to {self.config.web_base} to let automations work on this PC."),
        )

    # --- Buttons (Tk thread) -----------------------------------------------------------------------

    def _stop(self) -> None:
        runner = self.runner
        if runner is not None:
            runner.stop()

    def _sign_in(self) -> None:
        if self._sign_in_session is not None:
            self._sign_in_session.cancel()
        try:
            session = SignInSession(self.config.web_base)
        except (SignInError, OSError) as error:
            return self._show_account(f"Could not start sign-in: {error}")
        self._sign_in_session = session
        self._show_account("Waiting for the browser… finish signing in there.")

        def wait() -> None:
            try:
                session.start()
                result = session.wait()
            except SignInError as error:
                if self._sign_in_session is session:
                    self._show_account(str(error))
                return
            finally:
                if self._sign_in_session is session:
                    self._sign_in_session = None
            self.config = config.Config(
                web_base=self.config.web_base, api_base=self.config.api_base, access_token=result.access_token,
                org_id=result.org_id, user_id=result.user_id, email=result.email, consented=self.config.consented)
            config.save(self.config)
            self._show_account()

        threading.Thread(target=wait, name="mindloom-sign-in-wait", daemon=True).start()

    def _sign_out(self) -> None:
        self._stop()
        self.config = self.config.signed_out()
        config.save(self.config)
        self._show_account()

    def _quit(self) -> None:
        self._stop()
        self.quit.set()
        self.window.close()

    # --- The worker ----------------------------------------------------------------------------------

    def _consented(self) -> bool:
        return self.config.consented

    def _set_consented(self) -> None:
        self.config.consented = True
        config.save(self.config)

    def _work(self) -> None:
        # The driver joins COM on this thread, so it is made here and not on the Tk thread.
        from .driver import WindowsDriver

        try:
            driver = WindowsDriver()
        except Exception:  # noqa: BLE001
            logger.exception("UI Automation is not available")
            return self._show_account("Windows UI Automation could not be started on this PC.")
        while not self.quit.is_set():
            current = self.config
            if not current.signed_in:
                self.runner = None
                self.quit.wait(1)
                continue
            runner = Runner(ApiClient(current.api_base, current.access_token), driver, self.window,
                            consented=self._consented, set_consented=self._set_consented)
            self.runner = runner
            expired = False
            while not self.quit.is_set() and self.config is current:
                try:
                    runner.poll_once()
                except Exception:  # noqa: BLE001 - one bad task must not end the app
                    logger.exception("A device task failed")
                    runner.active_task_id = None
                now_expired = runner.last_poll_error is not None and runner.last_poll_error.status == 401
                if now_expired != expired:
                    expired = now_expired
                    self._show_account("Your sign-in ran out. Sign out, then sign in again." if expired else None)
                self.quit.wait(PENDING_POLL_SECONDS)

    def run(self) -> None:
        from . import win32

        win32.start_stop_hotkey(self._stop)
        threading.Thread(target=self._work, name="mindloom-runner", daemon=True).start()
        self.window.run()
        self.quit.set()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--version"]:
        print(__version__)
        return 0
    if sys.platform != "win32":
        print("MindLoom for Windows only runs on Windows.")
        return 2
    if args[:1] == ["--driver-selftest"]:
        from .selftest import run

        return run(args[1:])
    directory = config.config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # Errors only: never screen content, typed text or tokens.
    logging.basicConfig(filename=str(directory / "agent.log"), level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    AgentApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
