"""Workbook connectors: uploaded file, server folder, Microsoft Graph drive.

Every connector returns workbook bytes held only in memory. Server folders are
restricted to the roots configured in SOURCE_ROOTS (no arbitrary paths).
"""

import logging
import os
from pathlib import Path
from urllib.parse import quote

import requests
from django.conf import settings

from apps.core import m365
from apps.sources.models import DataSource

logger = logging.getLogger("qa.sources")

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
HTTP_TIMEOUT = 30


class SourceError(Exception):
    """User-safe error message (never contains secrets)."""


# --- Uploaded workbook ---------------------------------------------------------

def upload_path(source: DataSource) -> Path:
    return Path(settings.PRIVATE_UPLOAD_ROOT) / f"source_{source.pk}.xlsx"


def save_upload(source: DataSource, uploaded_file):
    name = os.path.basename(uploaded_file.name or "")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise SourceError("Only .xlsx and .xlsm workbooks are supported.")
    if uploaded_file.size > settings.MAX_WORKBOOK_BYTES:
        raise SourceError("The file is too large.")
    target = upload_path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        for chunk in uploaded_file.chunks():
            fh.write(chunk)
    os.replace(tmp, target)  # replaces the previous copy; only the current file is kept
    return name[:255]


# --- Server folder ---------------------------------------------------------------

def _root(name):
    roots = settings.SOURCE_ROOTS
    if name not in roots:
        raise SourceError("The selected server folder root is not configured on the server.")
    return Path(roots[name]).resolve()


def safe_server_path(root_name, relative):
    root = _root(root_name)
    relative = (relative or "").replace("\\", "/").lstrip("/")
    if "\x00" in relative:
        raise SourceError("Invalid path.")
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise SourceError("The path is outside the configured folder.")
    return candidate


def browse_server_folder(root_name, relative=""):
    folder = safe_server_path(root_name, relative)
    if not folder.exists():
        raise SourceError("The folder does not exist or the application server cannot access it.")
    if not folder.is_dir():
        raise SourceError("The path is not a folder.")
    root = _root(root_name)
    entries = []
    try:
        for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith((".", "~$")):
                continue
            if child.is_dir() or child.suffix.lower() in ALLOWED_EXTENSIONS:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child.relative_to(root)).replace("\\", "/"),
                    "type": "folder" if child.is_dir() else "file",
                    "size": None if child.is_dir() else stat.st_size,
                    "modified": int(stat.st_mtime),
                })
    except PermissionError:
        raise SourceError("The application server's service account does not have permission to read this folder.")
    return entries


def _read_server_file(config):
    path = safe_server_path(config.get("root", ""), config.get("path", ""))
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise SourceError("Only .xlsx and .xlsm workbooks are supported.")
    if not path.is_file():
        raise SourceError("The workbook was not found, or the application server cannot access it.")
    try:
        if path.stat().st_size > settings.MAX_WORKBOOK_BYTES:
            raise SourceError("The workbook exceeds the maximum allowed size.")
        with open(path, "rb") as fh:
            return fh.read()
    except PermissionError:
        raise SourceError("The application server's service account does not have permission to read this workbook.")
    except OSError:
        raise SourceError("The workbook could not be read (it may be locked or the share unavailable).")


# --- Microsoft Graph ---------------------------------------------------------------

def graph_configured():
    return m365.configured()


def graph_token():
    try:
        return m365.access_token()
    except m365.M365Error as exc:
        raise SourceError(str(exc))


def _graph_get(path, params=None, stream=False):
    try:
        resp = requests.get(f"{GRAPH_BASE}{path}", headers={"Authorization": f"Bearer {graph_token()}"},
                            params=params, timeout=HTTP_TIMEOUT, stream=stream)
    except requests.RequestException:
        raise SourceError("Could not reach Microsoft Graph. Check the server's outbound network access.")
    if resp.status_code in (401, 403):
        raise SourceError("Access denied by Microsoft Graph. The app needs Files.Read.All or Sites.Selected access to this location.")
    if resp.status_code == 404:
        raise SourceError("The file or folder was not found in OneDrive/SharePoint.")
    if resp.status_code >= 400:
        raise SourceError(f"Microsoft Graph returned an error (HTTP {resp.status_code}).")
    return resp


def _drive_base(config):
    if config.get("drive_id"):
        return f"/drives/{quote(config['drive_id'])}"
    if config.get("site_id"):
        return f"/sites/{quote(config['site_id'])}/drive"
    if config.get("user"):
        return f"/users/{quote(config['user'])}/drive"
    raise SourceError("Specify a drive ID, SharePoint site ID or OneDrive user.")


def resolve_share_link(url):
    """Turn a OneDrive/SharePoint link (copied from 'Share' or the address bar) into drive + item IDs."""
    url = (url or "").strip()
    if not url.lower().startswith("https://") or not any(h in url.lower() for h in ("sharepoint.com", "onedrive.live.com", "1drv.ms")):
        raise SourceError("Paste a OneDrive or SharePoint link (https://...sharepoint.com/...).")
    item = _graph_get(f"/shares/{m365.share_id(url)}/driveItem", params={"$select": "id,name,file,parentReference,webUrl"}).json()
    if "file" not in item:
        raise SourceError("The link points to a folder. Share the Excel file itself.")
    if Path(item.get("name", "")).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise SourceError("The linked file is not an .xlsx or .xlsm workbook.")
    return {"drive_id": item["parentReference"]["driveId"], "item_id": item["id"], "display_path": item.get("name", ""),
            "web_url": item.get("webUrl", "")[:500]}


def graph_search_sites(term):
    data = _graph_get("/sites", params={"search": term[:100]}).json()
    return [{"id": s["id"], "name": s.get("displayName") or s.get("name"), "url": s.get("webUrl")} for s in data.get("value", [])]


def graph_list_drives(site_id=None, user=None):
    path = f"/sites/{quote(site_id)}/drives" if site_id else f"/users/{quote(user or '')}/drives"
    data = _graph_get(path).json()
    return [{"id": d["id"], "name": d.get("name"), "url": d.get("webUrl")} for d in data.get("value", [])]


def graph_browse(config, item_id=None):
    base = _drive_base(config)
    path = f"{base}/items/{quote(item_id)}/children" if item_id else f"{base}/root/children"
    data = _graph_get(path, params={"$top": "200", "$select": "id,name,folder,file,size,lastModifiedDateTime"}).json()
    entries = []
    for item in data.get("value", []):
        is_folder = "folder" in item
        if is_folder or Path(item.get("name", "")).suffix.lower() in ALLOWED_EXTENSIONS:
            entries.append({"id": item["id"], "name": item["name"], "type": "folder" if is_folder else "file",
                            "size": item.get("size"), "modified": item.get("lastModifiedDateTime")})
    return sorted(entries, key=lambda e: (e["type"] != "folder", e["name"].lower()))


def _read_graph_file(config):
    base = _drive_base(config)
    if config.get("item_id"):
        path = f"{base}/items/{quote(config['item_id'])}/content"
    elif config.get("item_path"):
        path = f"{base}/root:/{quote(config['item_path'].lstrip('/'))}:/content"
    else:
        raise SourceError("No workbook has been selected for this source.")
    resp = _graph_get(path, stream=True)
    chunks, total = [], 0
    for chunk in resp.iter_content(1024 * 256):
        total += len(chunk)
        if total > settings.MAX_WORKBOOK_BYTES:
            raise SourceError("The workbook exceeds the maximum allowed size.")
        chunks.append(chunk)
    return b"".join(chunks)


# --- Public API ------------------------------------------------------------------------

def fetch_workbook(source: DataSource) -> bytes:
    if source.provider == DataSource.PROVIDER_UPLOAD:
        path = upload_path(source)
        if not path.exists():
            raise SourceError("No workbook has been uploaded for this source yet.")
        return path.read_bytes()
    if source.provider == DataSource.PROVIDER_SERVER_FOLDER:
        return _read_server_file(source.config or {})
    if source.provider == DataSource.PROVIDER_GRAPH:
        return _read_graph_file(source.config or {})
    raise SourceError("Unsupported source provider.")
