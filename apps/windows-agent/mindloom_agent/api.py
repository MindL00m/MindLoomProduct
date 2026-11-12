"""The device-task calls of the MindLoom API (``apps/api/api/routes/captures.py``)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlsplit

SURFACE = "windows"


class ApiError(Exception):
    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"MindLoom API error {status}: {detail[:300]}")
        self.status = status
        self.detail = detail


class NotSignedIn(ApiError):
    def __init__(self) -> None:
        super().__init__(401, "Not signed in.")


class ApiClient:
    def __init__(self, api_base: str, access_token: str, timeout: float = 90.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.access_token = access_token.strip()
        # A step waits for the model to choose an action, which can take a while.
        self.timeout = timeout

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self.access_token:
            raise NotSignedIn()
        split = urlsplit(self.api_base)
        # The token only travels over https, or to this PC during development.
        if split.scheme != "https" and split.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ApiError(0, "The MindLoom address must be https.")
        request = urllib.request.Request(self.api_base + path, method=method)
        request.add_header("Authorization", f"Bearer {self.access_token}")
        request.add_header("Accept", "application/json")
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, data=data, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, error.read().decode("utf-8", "replace")) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise ApiError(0, str(error)) from None

    @staticmethod
    def _id(task_id: str) -> str:
        return quote(str(task_id), safe="")

    def list_tasks(self, status: str = "pending") -> list[dict[str, Any]]:
        tasks = self._send("GET", f"/captures/device-tasks?status={quote(status)}&surface={SURFACE}")
        return tasks if isinstance(tasks, list) else []

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._send("GET", f"/captures/device-tasks/{self._id(task_id)}")

    def claim(self, task_id: str) -> dict[str, Any]:
        return self._send("POST", f"/captures/device-tasks/{self._id(task_id)}/claim?surface={SURFACE}")

    def step(self, task_id: str, observation: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
        """The current screen and the last action's result in; the next action out."""

        return self._send("POST", f"/captures/device-tasks/{self._id(task_id)}/step",
                          {"observation": observation, "result": result})

    def reply(self, task_id: str, action: str, note: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"action": action}
        if note:
            body["note"] = note[:2000]
        return self._send("POST", f"/captures/device-tasks/{self._id(task_id)}/reply", body)

    def stop(self, task_id: str) -> dict[str, Any]:
        return self._send("POST", f"/captures/device-tasks/{self._id(task_id)}/stop")
