"""The window people see: sign-in and status, and the overlay that shows what MindLoom is
doing during a task, with Stop always on it.

Tk runs on the main thread; the runner calls the ``show_*`` methods from its own thread,
so each one only queues a change that the Tk loop applies. Button presses go back to the
runner through :meth:`take_choice`, except Stop, which also calls ``on_stop`` at once.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable

# The overlay's buttons per state: (label, choice).
_BUTTONS = {
    "working": [],
    "consent": [("Allow", "allow"), ("Not now", "not_now")],
    "approval": [("Approve", "approve"), ("Reject", "reject")],
    "ask": [("Done", "done"), ("I can't", "cant")],
    "countdown": [("Not now", "not_now")],
    "paused": [("Resume", "resume")],
    "finished": [("Close", "close")],
    "notice": [("Close", "close")],
}


class OverlayWindow:
    def __init__(self, on_stop: Callable[[], None], on_sign_in: Callable[[], None],
                 on_sign_out: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_stop = on_stop
        self._changes: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._choices: "queue.Queue[str]" = queue.Queue()

        self.root = tk.Tk()
        self.root.title("MindLoom for Windows")
        self.root.geometry("380x170")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.root.iconify)  # closing keeps it running
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        self._account = ttk.Label(frame, text="Not signed in.", wraplength=350, justify="left")
        self._account.pack(anchor="w")
        self._status = ttk.Label(frame, text="", wraplength=350, justify="left")
        self._status.pack(anchor="w", pady=(6, 10))
        row = ttk.Frame(frame)
        row.pack(anchor="w")
        self._sign_in = ttk.Button(row, text="Sign in", command=on_sign_in)
        self._sign_in.pack(side="left")
        self._sign_out = ttk.Button(row, text="Sign out", command=on_sign_out)
        ttk.Button(row, text="Quit", command=on_quit).pack(side="right", padx=(8, 0))
        ttk.Label(frame, text="Stop a running automation at any time: Ctrl+Alt+.", foreground="#555").pack(
            anchor="w", pady=(12, 0))

        # The overlay: small, always on top, bottom right, never takes the keyboard focus.
        self.panel = tk.Toplevel(self.root)
        self.panel.withdraw()
        self.panel.title("MindLoom")
        self.panel.attributes("-topmost", True)
        try:
            self.panel.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        self.panel.protocol("WM_DELETE_WINDOW", lambda: None)
        body = ttk.Frame(self.panel, padding=12)
        body.pack(fill="both", expand=True)
        self._title = ttk.Label(body, text="", font=("Segoe UI", 10, "bold"), wraplength=320, justify="left")
        self._title.pack(anchor="w")
        self._line = ttk.Label(body, text="", wraplength=320, justify="left")
        self._line.pack(anchor="w", pady=(4, 8))
        self._buttons = ttk.Frame(body)
        self._buttons.pack(anchor="w")
        self.root.after(100, self._drain)

    # --- Tk thread ---------------------------------------------------------------------------------

    def _drain(self) -> None:
        try:
            while True:
                self._changes.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _choose(self, choice: str) -> None:
        if choice == "close":
            self.panel.withdraw()
            return
        if choice == "stop":
            self._on_stop()
        self._choices.put(choice)

    def _show(self, state: str, title: str, line: str, with_stop: bool = True) -> None:
        def apply() -> None:
            self._title.configure(text=title[:160])
            self._line.configure(text=line[:600])
            for child in self._buttons.winfo_children():
                child.destroy()
            for label, choice in _BUTTONS[state]:
                ttk.Button(self._buttons, text=label, command=lambda c=choice: self._choose(c)).pack(
                    side="left", padx=(0, 6))
            if with_stop:
                ttk.Button(self._buttons, text="Stop", command=lambda: self._choose("stop")).pack(side="left")
            self.panel.update_idletasks()
            width, height = 360, max(110, self.panel.winfo_reqheight())
            x = self.panel.winfo_screenwidth() - width - 24
            y = self.panel.winfo_screenheight() - height - 80
            self.panel.geometry(f"{width}x{height}+{x}+{y}")
            if self.panel.state() == "withdrawn":
                self.panel.deiconify()

        self._changes.put(apply)

    def set_account(self, email: str | None, status: str = "") -> None:
        def apply() -> None:
            self._account.configure(text=f"Signed in as {email}." if email else "Not signed in.")
            self._status.configure(text=status)
            if email:
                self._sign_in.pack_forget()
                self._sign_out.pack(side="left")
            else:
                self._sign_out.pack_forget()
                self._sign_in.pack(side="left")

        self._changes.put(apply)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        self._changes.put(self.root.destroy)

    # --- The runner's view (any thread) -------------------------------------------------------------

    def take_choice(self) -> str | None:
        try:
            return self._choices.get_nowait()
        except queue.Empty:
            return None

    def show_working(self, title: str, line: str, step: int) -> None:
        self._show("working", title, (f"Step {step}: " if step else "") + line)

    def show_consent(self, apps: list[str]) -> None:
        self._show("consent", "Let MindLoom work on this PC?",
                   f"An automation wants MindLoom to do steps in {', '.join(apps) or 'your apps'}. It only uses "
                   "the apps you allowed for that automation, asks before anything that can't be undone, and "
                   "never types passwords. You can stop it at any time with Stop or Ctrl+Alt+.", with_stop=False)

    def show_approval(self, title: str, description: str, reason: str | None) -> None:
        self._show("approval", f"Approve? {title}", description + (f"\n{reason}" if reason else ""))

    def show_ask(self, title: str, instruction: str) -> None:
        self._show("ask", f"Your turn: {title}", instruction)

    def show_countdown(self, title: str, app_name: str, seconds: int) -> None:
        self._show("countdown", title, f"Bringing {app_name} to the front in {seconds}…")

    def show_paused(self, title: str, app_name: str) -> None:
        self._show("paused", title, f"Paused: you're using {app_name}. Click Resume when MindLoom can carry on.")

    def show_finished(self, title: str, status: str, message: str | None) -> None:
        heading = {"completed": "Done", "failed": "Couldn't finish", "stopped": "Stopped"}.get(status, status)
        self._show("finished", f"{heading}: {title}", message or "", with_stop=False)

    def show_notice(self, title: str, message: str) -> None:
        self._show("notice", title, message, with_stop=False)

    def hide(self) -> None:
        self._changes.put(self.panel.withdraw)
