# Local Ubuntu / Stream Deck acceptance

**Not performed in the supplied build environment.** Record Ubuntu version, Python/package versions, firmware/serial, desktop session type and each observed result. Do not run potentially destructive scripts for these checks.

## 1. Installation and ownership

Install from a normal Ubuntu 24.04 desktop session. Replug `0fd9:006c`. Verify `sdlctl doctor`, `sdlctl devices`, and `sdlctl status`. Start a second physical Controller and confirm an actionable already-running error. Verify a competing Stream Deck app is not force-terminated. Re-run installer and confirm configuration/assets remain intact.

## 2. Physical mapping and rendering

Apply `examples/key-map.json`. Check all labels 0–31 follow left-to-right/top-to-bottom order and appear upright, not mirrored/rotated. Import asymmetric PNG, JPEG and SVG images, including an arrow with text, to check native JPEG orientation. Check combined icon/text, accents, long text, disabled and blank keys. Try global brightness 0, 40 and 100, then restore 50.

## 3. Navigation and stale input

Apply `examples/demo.json`. Root key 0 → Tools; key 1 → More; generated key 0 Back → Tools; Tools key 2 Home → root. Hold a key while calling `sdlctl navigate APPLIED_PAGE_UUID`; its release must not launch the new page's action. Repeat across Apply and Pause/Resume. A child Back key must remain functional even if another button's program fails.

## 4. USB disconnect/reconnect and held baseline

Enter a child page, unplug, change a label/brightness and Apply while disconnected. Reconnect and verify the same child page, latest images and brightness. A key held during reconnect must be released and pressed again. If the firmware sends no initial state report, the initial interaction may only establish released baselines; verify that no inferred click occurs. Repeat reconnect/suspend/resume and confirm no unexpected command launch or stale image writes.

## 5. Task lifetime

Apply `examples/execute-demo.json` only after reviewing it. Deliberately activate key 1; inspect `sdlctl executions --include-output`. Then create a temporary script that prints its PID and sleeps. Confirm timeout/cancel keep navigation responsive, and stopping the Controller terminates the task. Under the installed unit, repeat with:

```bash
systemctl --user kill --kill-whom=main --signal=SIGKILL sdl-controller.service
```

Use this only for the temporary test job. Confirm systemd restart occurs, the old job/process group is gone, its outcome is unknown after restart rather than rerun, and the deck returns to root. Record results separately from the automated foreground SIGKILL test.

## 6. Independent GUI application lifetime

Choose a known local installed application and its absolute path. Configure `mode: application`, `timeoutMs: null`, and review/Apply. Activate from an actual graphical session after `sdl-session`. Check its transient unit under `systemctl --user list-units 'sdl-app-*'`. Restart **only** `sdl-controller.service`; the application should remain running. Stop the Controller and verify the application is still independent. `launched` is acceptance, not proof the application completed work or opened a second window.

Repeat under Wayland and X11 only when those sessions are actually available. Do not claim results for an untested session type. A missing user manager must give a visible error rather than silently launching with task semantics.

## 7. Final operational checks

Verify opt-in graphical-login startup, actual session variable refresh, log rotation, backup/import without activation, and uninstall preserving data. Confirm screen locking does not imply automatic pause; explicitly pause where required. Measure page redraw/reconnect latency and idle CPU/RAM on the real machine; record observations instead of assuming specification targets were met.
