# MindLoom for Windows

Does the desktop-app steps of MindLoom automations ("Run with MindLoom") in native Windows
apps such as Tally, SAP GUI or Excel, through Windows UI Automation. It is the Windows
counterpart of the device-task part of `apps/desktop-agent`; the server side is
`apps/api/shuttle/windows_tasks.py` (surface `windows`).

> **Status: written on a Mac, not yet run on Windows.**
> Tested here (any system): the safety lists, key parsing, the gate, the outline, sign-in,
> config, and the task loop against the real API routes with a stand-in driver.
> Not tested anywhere: `driver.py`, `win32.py`, `ui.py`, `fixture/LoomFixture.cs` and the
> two PowerShell scripts. They have only been syntax-checked. Expect to fix things the
> first time `scripts\driver-selftest.ps1` runs, and do not ship before it passes.

## How it works

1. The person signs in (the browser opens `<site>/desktop-auth`, as for the Mac app).
2. The app polls `GET /captures/device-tasks?status=pending&surface=windows` and claims a
   task from the last 30 minutes. The first task asks for consent.
3. Loop: read the app as a numbered outline (`[e12] Button "Save"`), send it with the last
   result to `POST /captures/device-tasks/{id}/step`, get one action back, check it
   (`gate.py`), perform it, read again.
4. Approvals and "your turn" requests show in a small always-on-top window and on the web
   page. **Stop** there, or **Ctrl+Alt+.**, stops the task and its automation run.

An automation uses Windows when its computer is set to **A Windows PC** on the web page
(`skill_automations.device_os = 'windows'`).

## Safety, checked on this PC whatever the server sends

| Rule | Where |
| --- | --- |
| Only apps allowed for the automation; never the deny list (PowerShell, cmd, Registry Editor, Task Manager, Credential Manager, Windows Security, Settings, File Explorer, password managers, MindLoom itself…) | `policy.py`, `driver._app` |
| Irreversible labels, menu items and key combos need `approved: true`; so does Enter on an irreversible default or focused button | `gate.check`, `gate.check_focus_now` |
| Password fields are never read, filled or typed into, approved or not | `gate.check`, `driver._node`, `driver._set_value`, `driver._keys` |
| The Windows key, Alt+Tab, Ctrl+Shift+Esc and similar are never pressed | `keys.blocked_reason` |
| The target must still carry the label the action was chosen against | `gate.check` |
| Keys are only sent while the allowed app is the foreground window | `driver._keys` |
| Coming to the front is announced and can be declined; the previous window is put back | `runner._countdown` |
| The token is stored DPAPI-encrypted and only sent over https (or to localhost) | `config.py`, `api.py` |

The lists in `policy.py` are the server's. `apps/api/tests/test_windows_tasks.py` reads
that file and fails when they drift, so keep each list a plain tuple of quoted strings.

## Layout

```text
mindloom_agent/
  policy.py    safety lists (kept in step with the server by a test)
  keys.py      key combos and virtual-key codes
  outline.py   element tree → numbered outline
  gate.py      the check before every action
  runner.py    poll, claim, step loop, consent, approvals, Stop
  api.py       the device-task endpoints
  auth.py      browser sign-in (loopback listener)
  config.py    %APPDATA%\MindLoom\agent.json
  driver.py    UI Automation through comtypes        (Windows only, untested)
  win32.py     windows, processes, foreground, keys  (Windows only, untested)
  ui.py        the window and the overlay (Tk)        (untested)
  selftest.py  --driver-selftest
fixture/LoomFixture.cs       fixture app for the self-test (untested)
scripts/driver-selftest.ps1  build the fixture, run the self-test
scripts/build.ps1            MindLoom.exe with PyInstaller
tests/                       run anywhere
```

## Develop and test

On any system (the Windows-only modules are not imported):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r ../api/requirements-dev.txt
```

```bash
.venv/bin/pytest
```

`tests/test_runner.py` drives the real API routes, so it needs the API's requirements; it
is skipped when they are missing.

On Windows 10 or 11 with Python 3.12:

```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\driver-selftest.ps1
```

The self-test builds "Loom Fixture" with the C# compiler that ships with Windows, then
checks reading the window, set value, press, the approval gate, the password gate, denied
apps, typed text, a shortcut and menu commands, and prints `SELFTEST PASSED`. Run it from
a signed-in desktop and leave the keyboard and mouse alone while it runs.

Run the app from source, against a local MindLoom (`http://localhost:5500`, API on 8000):

```powershell
.\.venv\Scripts\python -m mindloom_agent
```

Point it at another site with `MINDLOOM_WEB_BASE` (and `MINDLOOM_API_BASE`, default `/api`).
Run from source, the process is `python.exe`; the app also refuses its own process id.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -WebBase https://app.example.com
```

This runs the tests, then writes `dist\MindLoom\MindLoom.exe` with an `install.json` naming
the site. The exe is not signed and there is no installer yet.

## Known limits

- Apps that expose little or nothing to UI Automation (Tally draws its own screens) are
  driven with `key` and `type_text` only. There are no mouse clicks by position and no
  picture fallback.
- Microsoft Store apps are hosted by `ApplicationFrameHost`, which is denied because it
  also hosts Settings.
- An app running as administrator can't be read or operated from a normal session.
- `open_app` starts an app from a Start menu shortcut with that name, or `<name>.exe`. If
  the app's process goes by a different name, open it first or allow the process name.
- Classic menus are covered by the self-test; menu strips and ribbons rely on `press`.

## The relay (SAP inside your network)

MindLoom's servers cannot call an SAP system that only exists inside a company network.
This app can do it for them: `mindloom_agent/relay.py` collects the calls MindLoom has
parked for the signed-in person, makes them from this PC, and posts the answers back.

- It only calls the **one https address approved on this PC** (asked once in the app
  window, or given on the command line), only SAP's data-service paths, and only GET,
  POST and PATCH. It never follows a redirect and writes nothing to disk.
- It shows a **relay code**. Type it into MindLoom when connecting SAP "through MindLoom
  for Windows": MindLoom then hands SAP's sign-in only to this PC.
- Without a window, on any system with Python 3.12:

  ```
  python -m mindloom_agent --sign-in
  python -m mindloom_agent --relay https://sap.example.internal:44300
  ```

Like the rest of this app, the relay **has not been run on Windows**. Its logic is tested
on macOS (`tests/test_relay.py`); the app-window prompt that asks about an address is not.
