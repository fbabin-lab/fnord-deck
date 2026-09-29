import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path
from uuid import uuid4

from sdl_core.bundle import export_bundle, import_bundle
from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, dumps, read_json
from sdl_core.model import all_buttons, button, empty_configuration, require_valid, validate
from sdl_core.paths import Paths
from sdl_controller.controller import diagnostics
from sdl_controller.ipc import read_frame

from .client import Client


def print_json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def demo_configuration() -> dict:
    document = empty_configuration()
    document["name"] = "Stream Deck XL — Controller demo"
    root = document["pages"][0]
    tools, child = str(uuid4()), str(uuid4())
    root["buttons"] = [button(0, "Tools", {"type": "core.navigate", "pageId": tools}),
                       *[button(i, f"Key {i}") for i in range(1, 32)]]
    document["pages"].extend([
        {"id": tools, "name": "Tools", "parentPageId": root["id"], "buttons": [
            button(1, "More", {"type": "core.navigate", "pageId": child}),
            button(2, "Home", {"type": "core.home"}), button(3, "No scripts\nare running")]},
        {"id": child, "name": "More tools", "parentPageId": tools, "buttons": [
            button(1, "Home", {"type": "core.home"}), button(2, "Niveau 3\nAccents: éàç")]},
    ])
    return document


async def apply_document(client: Client, document: dict, args) -> dict:
    status = await client.call("system.snapshot")
    expected = getattr(args, "expected_revision", None)
    params = {"document": document, "expectedRevision": status["revision"] if expected is None else expected,
              "operationId": getattr(args, "operation_id", None) or str(uuid4())}
    # Emit the operation ID before sending a mutation so interrupted clients can resolve it.
    print(f"Apply operationId: {params['operationId']}", file=sys.stderr)
    try:
        return await client.call("configuration.apply", params)
    except SdlError as exc:
        if exc.code != "REVIEW_REQUIRED":
            raise
    review = await client.call("configuration.review", {"document": document})
    print("Executable actions in this configuration (Apply does not run them):", file=sys.stderr)
    print(json.dumps(review["actions"], indent=2, ensure_ascii=False), file=sys.stderr)
    approved = getattr(args, "approve_executables", False)
    if not approved and sys.stdin.isatty():
        approved = input("Type APPROVE to enable these actions on deliberate button clicks: ") == "APPROVE"
    if not approved:
        raise SdlError("REVIEW_REQUIRED", "Nothing was applied. Review the commands, then use --approve-executables to confirm.")
    params.update({"reviewToken": review["reviewToken"], "confirmed": True})
    return await client.call("configuration.apply", params)


async def run(args) -> None:
    if args.command == "init":
        path = Path(args.output)
        if path.exists():
            raise SdlError("FILE_EXISTS", "Refusing to overwrite an existing draft.")
        atomic_write(path, dumps(demo_configuration()))
        print(f"Created {path}. No configuration applied and no commands executed.")
        return
    if args.command == "validate":
        result = validate(read_json(Path(args.file)))
        print_json(result)
        if result["errors"]:
            raise SystemExit(2)
        return
    if args.command == "doctor" and args.offline:
        print_json(diagnostics())
        return
    paths = Paths.discover("simulator" if args.simulator else "default")
    async with Client(paths.socket) as client:
        command = args.command
        if command == "status":
            result = await client.call("system.snapshot")
        elif command in ("plugins", "plugin-rescan"):
            result = await client.call("plugins.list" if command == "plugins" else "plugins.rescan")
        elif command in ("plugin-approve", "plugin-enable", "plugin-disable", "plugin-restart"):
            params = {"pluginId": args.plugin_id, "pluginVersion": args.version}
            if command == "plugin-approve":
                catalog = await client.call("plugins.rescan")
                package = next((p for p in catalog["packages"] if p["pluginId"] == args.plugin_id and p["pluginVersion"] == args.version), None)
                if package is None:
                    raise SdlError("PLUGIN_UNAVAILABLE", "Package is not installed or its files are invalid.")
                print(json.dumps(package, indent=2, ensure_ascii=False), file=sys.stderr)
                print("Native plugins are trusted code, not a security sandbox. Approval can start already-applied visible instances.", file=sys.stderr)
                approved = args.yes or (sys.stdin.isatty() and input("Type APPROVE after reviewing this package: ") == "APPROVE")
                if not approved:
                    raise SdlError("CONFIRMATION_REQUIRED", "Nothing was approved.")
                params.update({k: package[k] for k in ("fingerprint", "interpreterIdentity")})
                params["confirmed"] = True
                result = await client.call("plugins.approve", params)
            elif command == "plugin-restart":
                result = await client.call("plugins.restart", params)
            else:
                params["enabled"] = command == "plugin-enable"
                result = await client.call("plugins.setEnabled", params)
        elif command in ("blank", "unblank"):
            result = await client.call("runtime.blank", {"blanked": command == "blank"})
        elif command == "doctor":
            result = await client.call("system.diagnostics")
        elif command == "get":
            result = await client.call("configuration.get", {"revision": args.revision} if args.revision is not None else {})
            if args.output:
                atomic_write(Path(args.output), json.dumps(result["document"], indent=2, ensure_ascii=False).encode() + b"\n")
                print(f"Wrote draft {args.output}; active revision is {result['revision']}.")
                return
        elif command == "apply":
            result = await apply_document(client, read_json(Path(args.file)), args)
        elif command == "review":
            result = await client.call("configuration.review", {"document": read_json(Path(args.file))})
        elif command == "asset-import":
            path = Path(args.file)
            if path.stat().st_size > 20 * 1024 * 1024:
                raise SdlError("ASSET_INVALID", "Image exceeds 20 MiB.")
            result = await client.import_asset(path.read_bytes(), path.name)
        elif command == "preview":
            result = await client.call("render.preview", {"pageId": args.page} if args.page else {})
            atomic_write(Path(args.output), base64.b64decode(result["data"], validate=True))
            print(f"Wrote upright preview {args.output}; no actions executed.")
            return
        elif command in ("pause", "resume"):
            result = await client.call("runtime.pause", {"paused": command == "pause"})
        elif command == "devices":
            result = await client.call("device.list")
        elif command == "retry":
            result = await client.call("device.retry")
        elif command in ("brightness", "select-device"):
            current = await client.call("configuration.get")
            document = current["document"]
            args.expected_revision = current["revision"]
            if command == "brightness":
                document["settings"]["brightnessPercent"] = args.percent
            else:
                serial = args.serial
                if serial is None:
                    snapshot = await client.call("system.snapshot")
                    serial = (snapshot["device"].get("descriptor") or {}).get("serialNumber")
                if not serial:
                    raise SdlError("DEVICE_UNAVAILABLE", "No connected serial number is available. Connect the intended deck alone or supply --serial.")
                document["target"]["serialNumber"] = serial
            result = await apply_document(client, document, args)
        elif command == "navigate":
            snapshot = await client.call("system.snapshot")
            result = await client.call("runtime.navigate", {"pageId": args.page, "expectedRevision": snapshot["revision"]})
        elif command == "test":
            if not args.yes:
                raise SdlError("CONFIRMATION_REQUIRED", "This runs the applied button's program. Add --yes only after reviewing it.")
            snapshot = await client.call("system.snapshot")
            operation = args.operation_id or str(uuid4())
            print(f"Execution operationId: {operation}", file=sys.stderr)
            result = await client.call("execution.test", {"buttonId": args.button, "expectedRevision": snapshot["revision"],
                                                         "confirmed": True, "operationId": operation})
        elif command == "executions":
            result = await client.call("execution.list", {"includeOutput": args.include_output, "limit": args.limit})
        elif command == "cancel":
            result = await client.call("execution.cancel", {"runId": args.run_id})
        elif command == "config-history":
            result = await client.call("configuration.history", {"limit": args.limit})
        elif command == "events":
            print_json(await client.call("events.subscribe", {"types": []}))
            while True:
                print_json(await read_frame(client.reader))
        elif command in ("press", "release", "click"):
            if not args.simulator:
                raise SdlError("SIMULATOR_REQUIRED", "Synthetic key input requires the --simulator option.")
            if command == "click":
                await client.call("simulator.key", {"keyIndex": args.key, "down": True})
                await asyncio.sleep(0.06)
                result = await client.call("simulator.key", {"keyIndex": args.key, "down": False})
            else:
                result = await client.call("simulator.key", {"keyIndex": args.key, "down": command == "press"})
        elif command in ("disconnect", "reconnect"):
            if not args.simulator:
                raise SdlError("SIMULATOR_REQUIRED", "Simulated connection changes require --simulator.")
            result = await client.call("simulator.connection", {"connected": command == "reconnect"})
        elif command == "export":
            document = (await client.call("configuration.get"))["document"]
            identifiers = {b["appearance"]["iconAssetId"] for _, b in all_buttons(document) if b["appearance"]["iconAssetId"]}
            assets = {identifier: await client.read_asset(identifier) for identifier in identifiers}
            atomic_write(Path(args.output), export_bundle(document, assets))
            print(f"Exported data-only bundle to {args.output}. No execution approvals or plugins included.")
            return
        elif command == "import":
            path = Path(args.file)
            if path.stat().st_size > 256 * 1024 * 1024:
                raise SdlError("LIMIT_EXCEEDED", "Bundle exceeds 256 MiB.")
            if Path(args.output).exists():
                raise SdlError("FILE_EXISTS", "Choose a new output filename for the imported draft.")
            document, assets = import_bundle(path.read_bytes())
            for identifier, raw in assets.items():
                result = await client.import_asset(raw, identifier)
                if result["assetId"] != identifier:
                    raise SdlError("ASSET_INVALID", "Imported asset hash mismatch.")
            atomic_write(Path(args.output), json.dumps(document, indent=2, ensure_ascii=False).encode() + b"\n")
            print(f"Imported draft to {args.output}. It is NOT applied. Review executable paths before Apply.")
            return
        else:
            raise SdlError("INVALID_COMMAND", "Unknown command.")
        print_json(result)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Local CLI for the standalone Stream Deck Controller.")
    result.add_argument("--simulator", action="store_true", help="Connect to the isolated simulator instance.")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("status", "pause", "resume", "devices", "retry", "events", "disconnect", "reconnect", "plugins", "plugin-rescan", "blank", "unblank"):
        commands.add_parser(name)
    for name in ("plugin-approve", "plugin-enable", "plugin-disable", "plugin-restart"):
        command = commands.add_parser(name)
        command.add_argument("plugin_id"); command.add_argument("version")
        if name == "plugin-approve":
            command.add_argument("--yes", action="store_true", help="Explicitly approve trusted native code after reviewing the package.")
    command = commands.add_parser("init"); command.add_argument("output")
    command = commands.add_parser("validate"); command.add_argument("file")
    command = commands.add_parser("doctor"); command.add_argument("--offline", action="store_true")
    command = commands.add_parser("get"); command.add_argument("--output"); command.add_argument("--revision", type=int)
    command = commands.add_parser("apply"); command.add_argument("file")
    command.add_argument("--approve-executables", action="store_true")
    command.add_argument("--expected-revision", type=int); command.add_argument("--operation-id")
    command = commands.add_parser("review"); command.add_argument("file")
    command = commands.add_parser("asset-import"); command.add_argument("file")
    command = commands.add_parser("preview"); command.add_argument("output"); command.add_argument("--page")
    command = commands.add_parser("brightness"); command.add_argument("percent", type=int, choices=range(101), metavar="0..100")
    command = commands.add_parser("select-device"); command.add_argument("--serial")
    command = commands.add_parser("navigate"); command.add_argument("page")
    command = commands.add_parser("test"); command.add_argument("button")
    command.add_argument("--yes", action="store_true"); command.add_argument("--operation-id")
    command = commands.add_parser("executions"); command.add_argument("--include-output", action="store_true"); command.add_argument("--limit", type=int, default=20)
    command = commands.add_parser("cancel"); command.add_argument("run_id")
    command = commands.add_parser("config-history"); command.add_argument("--limit", type=int, default=20)
    for name in ("press", "release", "click"):
        command = commands.add_parser(name); command.add_argument("key", type=int)
    command = commands.add_parser("export"); command.add_argument("output")
    command = commands.add_parser("import"); command.add_argument("file"); command.add_argument("output")
    return result


def main() -> None:
    try:
        asyncio.run(run(parser().parse_args()))
    except SdlError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        if exc.details is not None:
            print(json.dumps(exc.details, indent=2, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    except (OSError, ValueError, TimeoutError) as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
