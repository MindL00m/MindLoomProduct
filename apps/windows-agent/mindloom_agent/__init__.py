"""MindLoom for Windows: does the desktop-app steps of MindLoom automations on this PC.

The API decides each action (``apps/api/shuttle/windows_tasks.py``); this app checks it
again (:mod:`mindloom_agent.gate`), performs it through Windows UI Automation
(:mod:`mindloom_agent.driver`) and reports the result.
"""

__version__ = "0.1.0"
