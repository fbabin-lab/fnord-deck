"""Transactional editor model. No widgets, USB, network, or process execution.

Every committed editor operation leaves a schema-valid rooted tree. Undo/redo retains
opaque extension data. A compact serialized history has both count and byte budgets.
"""
from __future__ import annotations

import copy
import time
from collections.abc import Callable
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest, dumps, loads
from sdl_core.model import button, empty_configuration, pages_by_id, require_valid

HISTORY_ENTRIES = 100
HISTORY_BYTES = 64 * 1024 * 1024


def fail(message: str, code: str = "INVALID_EDIT") -> None:
    raise SdlError(code, message)


def asset_ids(document: dict) -> set[str]:
    return {b["appearance"]["iconAssetId"] for p in document["pages"] for b in p["buttons"]
            if b["appearance"]["iconAssetId"]}


def renew_identities(document: dict, *, imported: bool = True) -> dict:
    """Imported data never inherits local executable approvals or device selection."""
    document = copy.deepcopy(document)
    require_valid(document)
    page_ids = {p["id"]: str(uuid4()) for p in document["pages"]}
    document["configurationId"] = str(uuid4())
    document["rootPageId"] = page_ids[document["rootPageId"]]
    if imported:
        document["target"]["serialNumber"] = None
    for page in document["pages"]:
        page["id"] = page_ids[page["id"]]
        if page["parentPageId"]:
            page["parentPageId"] = page_ids[page["parentPageId"]]
        for item in page["buttons"]:
            item["id"] = str(uuid4())
            if item["action"]["type"] == "core.navigate":
                item["action"]["pageId"] = page_ids[item["action"]["pageId"]]
            if item.get("pluginBinding"):
                item["pluginBinding"]["instanceId"] = str(uuid4())
                if imported:
                    item["pluginBinding"]["secretRefs"] = {}
    return document


class Draft:
    def __init__(self, document: dict | None = None, *, base_revision: int | None = None,
                 base_hash: str | None = None, source_socket: str | None = None) -> None:
        self.document = copy.deepcopy(document if document is not None else empty_configuration())
        require_valid(self.document)
        self.base_revision = base_revision
        self.base_hash = base_hash
        self.source_socket = source_socket
        self.saved_hash = digest(self.document)
        self.undo_stack: list[tuple[str, bytes]] = []
        self.redo_stack: list[tuple[str, bytes]] = []
        self._merge_key: str | None = None
        self._merge_time = 0.0
        self.generation = 0

    @property
    def dirty(self) -> bool:
        return digest(self.document) != self.saved_hash

    @property
    def differs_from_applied(self) -> bool:
        return digest(self.document) != self.base_hash

    def mark_saved(self, saved_hash: str | None = None) -> None:
        self.saved_hash = saved_hash or digest(self.document)

    def mark_applied(self, revision: int, document: dict, socket: str) -> None:
        self.base_revision = revision
        self.base_hash = digest(document)
        self.source_socket = socket

    def page(self, page_id: str) -> dict:
        page = pages_by_id(self.document).get(page_id)
        if page is None:
            fail("Section no longer exists.")
        return page

    def item(self, page_id: str, index: int) -> dict | None:
        return next((b for b in self.page(page_id)["buttons"] if b["keyIndex"] == index), None)

    def _slot(self, doc: dict, page_id: str, index: int) -> dict:
        page = pages_by_id(doc).get(page_id)
        count = doc["layout"]["rows"] * doc["layout"]["columns"]
        if page is None or not isinstance(index, int) or not 0 <= index < count:
            fail("Invalid button position.")
        if page["parentPageId"] is not None and index == 0:
            fail("The first button is reserved for Back.", "RESERVED_BACK")
        return page

    def _trim(self) -> None:
        while len(self.undo_stack) > HISTORY_ENTRIES or sum(len(x[1]) for x in self.undo_stack) > HISTORY_BYTES:
            self.undo_stack.pop(0)

    def change(self, label: str, operation: Callable[[dict], object], merge: str | None = None):
        candidate = copy.deepcopy(self.document)
        result = operation(candidate)
        require_valid(candidate)
        before, after = dumps(self.document), dumps(candidate)
        if before == after:
            return result
        now = time.monotonic()
        if not (merge and merge == self._merge_key and now - self._merge_time < 0.8 and self.undo_stack):
            self.undo_stack.append((label, before))
        self._merge_key, self._merge_time = merge, now
        self._trim()
        self.redo_stack.clear()
        self.document = candidate
        self.generation += 1
        return result

    def undo(self) -> None:
        if self.undo_stack:
            label, previous = self.undo_stack.pop()
            self.redo_stack.append((label, dumps(self.document)))
            self.document = loads(previous)
            self.generation += 1
            self._merge_key = None

    def redo(self) -> None:
        if self.redo_stack:
            label, following = self.redo_stack.pop()
            self.undo_stack.append((label, dumps(self.document)))
            self.document = loads(following)
            self.generation += 1
            self._merge_key = None

    def appearance(self, page_id: str, index: int, values: dict) -> None:
        def operation(doc):
            page = self._slot(doc, page_id, index)
            item = next((b for b in page["buttons"] if b["keyIndex"] == index), None)
            if item is None:
                item = button(index, "")
                page["buttons"].append(item)
            item["appearance"].update(copy.deepcopy(values))
        self.change("appearance", operation, f"appearance:{page_id}:{index}")

    def enabled(self, page_id: str, index: int, enabled: bool) -> None:
        def operation(doc):
            page = self._slot(doc, page_id, index)
            item = next((b for b in page["buttons"] if b["keyIndex"] == index), None)
            if item is None:
                item = button(index, "")
                page["buttons"].append(item)
            item["enabled"] = enabled
        self.change("enabled", operation)

    def set_action(self, page_id: str, index: int, action: dict) -> None:
        def operation(doc):
            page = self._slot(doc, page_id, index)
            item = next((b for b in page["buttons"] if b["keyIndex"] == index), None)
            if item is None:
                item = button(index, "")
                page["buttons"].append(item)
            if item["action"]["type"] == "core.navigate":
                fail("Remove or move the subsection before replacing its navigation action.")
            if action["type"] == "core.navigate":
                fail("Use Create subsection to create a valid navigation link.")
            item["action"] = copy.deepcopy(action)
        self.change("action", operation)

    def add_section(self, parent_id: str, index: int, name: str) -> str:
        def operation(doc):
            parent = self._slot(doc, parent_id, index)
            item = next((b for b in parent["buttons"] if b["keyIndex"] == index), None)
            if item is not None and item["action"]["type"] != "core.none":
                fail("Choose an empty button or a button with no action.")
            child = {"id": str(uuid4()), "name": name, "parentPageId": parent_id, "buttons": []}
            doc["pages"].append(child)
            if item is None:
                item = button(index, name)
                parent["buttons"].append(item)
            elif not item["appearance"]["text"]:
                item["appearance"]["text"] = name
            item["action"] = {"type": "core.navigate", "pageId": child["id"]}
            return child["id"]
        return self.change("add_section", operation)

    def rename_section(self, page_id: str, name: str) -> None:
        self.change("rename_section", lambda d: pages_by_id(d)[page_id].update(name=name))

    def descendants(self, page_id: str, document: dict | None = None) -> set[str]:
        document = document or self.document
        found = {page_id}
        pending = [page_id]
        children: dict[str, list[str]] = {}
        for p in document["pages"]:
            children.setdefault(p["parentPageId"], []).append(p["id"])
        while pending:
            for child in children.get(pending.pop(), []):
                if child not in found:
                    found.add(child)
                    pending.append(child)
        return found

    def remove(self, page_id: str, index: int) -> None:
        def operation(doc):
            page = self._slot(doc, page_id, index)
            item = next((b for b in page["buttons"] if b["keyIndex"] == index), None)
            if item is None:
                return
            if item["action"]["type"] == "core.navigate":
                deleted = self.descendants(item["action"]["pageId"], doc)
                doc["pages"] = [p for p in doc["pages"] if p["id"] not in deleted]
            page["buttons"].remove(item)
        self.change("remove", operation)

    def move(self, source_page: str, source_index: int, target_page: str, target_index: int) -> None:
        """Swap slots in one page; cross-page moves require an empty destination."""
        def operation(doc):
            source = self._slot(doc, source_page, source_index)
            target = self._slot(doc, target_page, target_index)
            if source_page == target_page and source_index == target_index:
                return
            item = next((b for b in source["buttons"] if b["keyIndex"] == source_index), None)
            other = next((b for b in target["buttons"] if b["keyIndex"] == target_index), None)
            if item is None:
                fail("There is no button to move.")
            if source_page != target_page:
                if other:
                    fail("Cross-section moves require an empty destination.")
                if item["action"]["type"] == "core.navigate":
                    child_id = item["action"]["pageId"]
                    if target_page in self.descendants(child_id, doc):
                        fail("A subsection cannot be moved into itself or one of its descendants.")
                    pages_by_id(doc)[child_id]["parentPageId"] = target_page
                source["buttons"].remove(item)
                target["buttons"].append(item)
            elif other:
                other["keyIndex"] = source_index
            item["keyIndex"] = target_index
        self.change("move", operation)

    def copy(self, page_id: str, index: int) -> dict:
        item = self.item(page_id, index)
        if item is None:
            fail("There is no button to copy.")
        subpages = []
        if item["action"]["type"] == "core.navigate":
            ids = self.descendants(item["action"]["pageId"])
            subpages = [p for p in self.document["pages"] if p["id"] in ids]
        return copy.deepcopy({"button": item, "pages": subpages})

    def paste(self, payload: dict, page_id: str, index: int) -> None:
        def operation(doc):
            target = self._slot(doc, page_id, index)
            if any(b["keyIndex"] == index for b in target["buttons"]):
                fail("Paste requires an empty destination; clear the button first.")
            data = copy.deepcopy(payload)
            ids = {p["id"]: str(uuid4()) for p in data["pages"]}
            original_root = data["button"]["action"].get("pageId")
            for page in data["pages"]:
                old_id = page["id"]
                page["id"] = ids[old_id]
                page["parentPageId"] = page_id if old_id == original_root else ids[page["parentPageId"]]
            for item in [data["button"], *(b for p in data["pages"] for b in p["buttons"])]:
                item["id"] = str(uuid4())
                if item["action"]["type"] == "core.navigate":
                    item["action"]["pageId"] = ids[item["action"]["pageId"]]
                if item.get("pluginBinding"):
                    item["pluginBinding"]["instanceId"] = str(uuid4())
            data["button"]["keyIndex"] = index
            target["buttons"].append(data["button"])
            doc["pages"].extend(data["pages"])
        self.change("paste", operation)

    def settings(self, *, name: str, brightness: int, locale: str, serial: str | None) -> None:
        def operation(doc):
            doc["name"] = name
            doc["settings"] = {"brightnessPercent": brightness, "locale": locale}
            doc["target"]["serialNumber"] = serial
        self.change("settings", operation)

    def envelope(self) -> dict:
        return {"draftFormatVersion": "1.0", "document": copy.deepcopy(self.document),
                "baseRevision": self.base_revision, "baseHash": self.base_hash,
                "sourceSocket": self.source_socket, "savedHash": self.saved_hash}

    @classmethod
    def from_envelope(cls, data: dict) -> "Draft":
        if not isinstance(data, dict) or data.get("draftFormatVersion") != "1.0":
            fail("Unsupported editor draft format.", "DRAFT_INVALID")
        revision, base_hash, source = (data.get(k) for k in ("baseRevision", "baseHash", "sourceSocket"))
        if revision is not None and (type(revision) is not int or revision < 0):
            fail("Invalid base revision.", "DRAFT_INVALID")
        if base_hash is not None and (not isinstance(base_hash, str) or len(base_hash) != 64):
            fail("Invalid base hash.", "DRAFT_INVALID")
        if source is not None and not isinstance(source, str):
            fail("Invalid draft source.", "DRAFT_INVALID")
        draft = cls(data["document"], base_revision=revision, base_hash=base_hash, source_socket=source)
        saved = data.get("savedHash")
        if isinstance(saved, str) and len(saved) == 64:
            draft.saved_hash = saved
        return draft
