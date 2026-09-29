> Version 0.2.0 adds approved polling/display plugins and API 1.1. The [current plugin guide](../../docs/PLUGIN-HOST-0.2.0.md) supersedes earlier plugin-deferred statements below. Run both this document's baseline acceptance checks and the plugin guide's additional checks.

# Ubuntu 24.04 local acceptance

These checks are **pending** in the delivered release. Automated non-Qt results do not replace them. Run as your normal graphical desktop user. Begin with navigation-only layouts. Do not configure destructive commands for acceptance testing.

| Check | Procedure | Expected result |
|---|---|---|
| Installation | Run `./scripts/install-ubuntu.sh` without sudo | Only package installation requests elevated access; private Configurator venv and Applications entry appear; Controller files/service unchanged |
| Startup | Run `sdl-configurator --check`, then `sdl-configurator` | Qt imports and fonts available; window opens; no root requirement |
| Existing configuration | Start existing Controller with `sdl-session --start`; Load from Controller | Existing layout and managed images appear; no commands execute |
| Offline startup | Close editor, stop Controller through its normal tools, reopen editor | Recovered draft editable; clear unavailable banner; no automatic Apply on reconnect |
| Visual grid | Select all corners and inspect source preview | 32 keys in 8×4; key numbering 1–32; previews unobstructed with scroll/zoom |
| Icons | Import a JPEG, PNG with transparency and safe SVG; delete source files | Managed previews survive; fit/crop/stretch and overlay respond; invalid SVG gives an error |
| Text | Enter `Montréal`, accents and line breaks; change size/alignment | Shared preview updates; no unexpected markup; blank and multiline labels render |
| Sections | Create Tools → More; double-click sections/Back; add Home | Local navigation works; key 1 is reserved Back in every nonroot section; no physical navigation until requested/applied |
| Structural edits | Move/copy/delete a subtree, then Undo/Redo | Stable IDs on move; copies independent; no orphans/cycles; generated Back cannot move |
| Persistence | Save; change text without saving; close/reopen | Recovery restores unsaved draft; earlier named snapshot remains in Open saved draft |
| Apply cancellation | Review a navigation-only draft, then Cancel | Physical layout/revision unchanged; draft retained |
| Apply success | Review again, check acknowledgement, apply | Controller revision changes; device synchronization reported separately; no actions execute |
| Physical mapping | Apply corner labels, then use physical navigation/Back | Correct key/image orientation and mapping via existing Controller |
| Revision conflict | Load same revision in two isolated workspaces; apply first then second | Second refuses stale baseline; replacement requires explicit choice and another fresh review |
| Action edit | Set `/usr/bin/printf` as managed task, one literal argument `Hello from deck\n` | Editing/saving/Apply do not run it; normal grid clicks do not run it |
| Explicit run | Apply the harmless printf action; use Run applied action and confirm | One execution record; canceling confirmation creates no run |
| Simulator safety | Run `sdl-controller --simulate` and editor `--simulator` | Separate editor draft/cache and socket; Run disabled without explicit Controller execution enabling |
| Export/import | Export bundle; import into a new isolated editor workspace | Original images included; IDs renewed; imported content remains unapplied/unapproved |
| Controller independence | Close editor during idle, press a navigation button | Controller continues; no USB/service stop on editor exit |
| Language | Select French, restart; separately set deck locale to French | Interface labels in French; configured text untouched; generated navigation uses applied deck locale |
| HiDPI/Wayland | Test normal/maximized windows and desktop scale used on workstation | Inspector, dialogs, scrollbars and grid accessible; no clipping that blocks controls |
| Uninstall | Close editor and run uninstall script | Application/launcher removed; drafts/assets and existing Controller retained |

## Automated desktop tests

From the extracted source after installation:

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/streamdeck-linux-configurator/app/venv/bin/activate"
python -m pip install -r requirements-dev.lock
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui.py -q
```

The ten Qt tests exercise window construction, preview delivery, Unicode editing, local section/Back navigation, no execution from grid double-clicks, review acknowledgement, literal arguments/interpreter settings, recovery autosave, disabled simulator testing and French labels. They do not prove actual USB operation or all compositor/platform-plugin combinations.

For a failure report, include Python/Qt versions, desktop session type, the command used, a traceback (run from terminal), and the exact test name. Review logs for personal paths before sharing.
