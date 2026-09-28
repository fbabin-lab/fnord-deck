# Dependencies and licensing

Runtime dependencies are exactly version-pinned in `requirements.lock`; development dependencies are in `requirements-dev.lock`. No vendored dependencies, font files or binary USB drivers are included in this source release. The installer downloads Python packages from the configured pip index and Ubuntu packages from the configured apt repositories. Those external packages keep their own licenses.

Direct dependencies:

| Component | Version | Role |
|---|---|---|
| streamdeck / python-elgato-streamdeck | 0.10.0 | Direct USB HID device control and native image conversion |
| Pillow | 12.3.0 | Raster decoding, composition, typography and output |
| CairoSVG | 2.8.2 | Restricted SVG rasterization after application validation |
| defusedxml | 0.7.1 | XML parsing with entity/DTD protections |
| jsonschema | 4.26.0 | Configuration and API parameter validation |

The lock file includes the transitive Python dependency closure. The source project's license applies only to this project's code; it does not relicense dependencies. Consult installed package metadata and the authoritative upstream license files before redistributing an environment or installer that embeds third-party packages. The source ZIP and project-only wheel do not embed those packages.

Ubuntu system packages requested: `python3-venv`, `libhidapi-libusb0`, `libcairo2`, `fonts-dejavu-core`. systemd user services, udev and an active desktop-session user manager are assumed. Fonts are loaded from the host installation, not shipped in this project.

## Primary technical references

Verified during implementation on September 28, 2026:

- Elgato Stream Deck XL HID documentation: https://docs.elgato.com/streamdeck/hid/stream-deck-xl/
  - Model 20GAT9901; vendor/product IDs 0x0fd9/0x006c; 8 columns, 4 rows; 96x96 key images. Native orientation is delegated to the library helper rather than duplicated in this project.
- streamdeck 0.10.0 package: https://pypi.org/project/streamdeck/0.10.0/
- Device API: https://python-elgato-streamdeck.readthedocs.io/en/stable/modules/devices.html
- Linux backend setup: https://python-elgato-streamdeck.readthedocs.io/en/stable/pages/backend_libusb_hidapi.html
- DeviceManager transport source: https://github.com/abcminiuser/python-elgato-streamdeck/blob/master/src/StreamDeck/DeviceManager.py
  - The adapter passes the transport identifier `libusb`, not the implementation class name `LibUSBHIDAPI`.
- Reader/report source: https://github.com/abcminiuser/python-elgato-streamdeck/blob/master/src/StreamDeck/Devices/StreamDeck.py
  - This release wraps the private `_read_control_states` hook to observe full initial key reports, so the pinned dependency requires regression testing before upgrade.
- Image helper source: https://github.com/abcminiuser/python-elgato-streamdeck/blob/master/src/StreamDeck/ImageHelpers/PILHelper.py
- systemd 255 transient-service documentation source: https://github.com/systemd/systemd/blob/v255/man/systemd-run.xml
  - Application mode uses a separate user service, `--service-type=exec` and `--expand-environment=no`. This prevents implicit `$` expansion in the launcher command; configured argv remains literal.

Live upstream `master` documentation was inspected for integration details but was not downloaded or imported as an installed HID dependency in this environment. Fake-library tests exercise the adapter contract; hardware/backend acceptance remains a local requirement. A version pin is not a supply-chain audit. Version hashes are not locked in this release.
