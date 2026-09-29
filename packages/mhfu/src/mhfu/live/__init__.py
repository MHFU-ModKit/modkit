"""Driving the running game: a `Session` that launches or attaches, and flows built on it.

from mhfu.live import Session, boot, dialog

with Session.launch(cold=True) as s:
    boot.to_village(s)
    dialog.has_control(s)
"""

from .session import Launcher, Session

__all__ = ["Launcher", "Session"]
