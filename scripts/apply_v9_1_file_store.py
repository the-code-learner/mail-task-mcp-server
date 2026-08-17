from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


server_path = Path("src/postmaster/server.py")
text = server_path.read_text()

text = replace_once(text, "from urllib.parse import urlencode", "from urllib.parse import quote, urlencode", "urllib import")
text = replace_once(
    text,
    "from .context_engine import ContextEngine\n",
    "from .context_engine import ContextEngine\nfrom .file_store import FileStore, FileStoreError\n",
    "file store import",
)
text = text.replace("v9.0 adds persistent project memory/skills", "v9.1 adds persistent project memory/skills and a scoped small-file store")
text = replace_once(
    text,
    "@lru_cache(maxsize=1)\ndef context_engine() -> ContextEngine:\n    return ContextEngine()\n\ndef _safe_call",
    "@lru_cache(maxsize=1)\ndef context_engine() -> ContextEngine:\n    return ContextEngine()\n\n\n@lru_cache(maxsize=1)\ndef file_store() -> FileStore:\n    return FileStore()\n\ndef _safe_call",
    "file store cache",
)
text = replace_once(
    text,
    "except (MailBridgeError, SchedulerError, AccountStoreError, AnalyticsError, KnowledgeError, SemanticError) as exc:",
    "except (MailBridgeError, SchedulerError, AccountStoreError, AnalyticsError, KnowledgeError, SemanticError, FileStoreError) as exc:",
    "safe call errors",
)
text = replace_once(
    text,
    '    """Read-only. Return the running bridge build and high-level v9.0 capabilities."""\n    return {\n        "ok": True,\n        "build": os.getenv("BRIDGE_BUILD", "unknown"),',
    '    """Read-only. Return the running bridge build and high-level v9.1 capabilities."""\n    return {\n        "ok": True,\n        "build": os.getenv("BRIDGE_BUILD") or os.getenv("POSTMASTER_REF") or "unknown",',
    "build fallback",
)
text = replace_once(
    text,
    '        "optional_model2vec": True,\n    }',
    '        "optional_model2vec": True,\n        "small_file_store": True,\n    }',
    "build file capability",
)

file_tools = r'''

# -------------------------
# Persistent small files (v9.1)
# -------------------------
@mcp.tool()
def file_store_status():
    """Read-only. Return persistent small-file store status and configured limits."""
    return _safe_call(file_store().status)


@mcp.tool()
def save_file(
    owner_id: str,
    filename: str,
    content_base64: str,
    project_id: str | None = None,
    media_type: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
):
    """WRITE ACTION. Save one small binary file from base64 into the persistent scoped file store."""
    try:
        _require_knowledge_scope(owner_id, project_id)
        return file_store().save_base64(
            owner_id=owner_id, project_id=project_id, filename=filename,
            content_base64=content_base64, media_type=media_type,
            description=description, tags=tags or [],
        )
    except (FileStoreError, SchedulerError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def save_text_file(
    owner_id: str,
    filename: str,
    content: str,
    project_id: str | None = None,
    media_type: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
):
    """WRITE ACTION. Save a small UTF-8 text file without requiring base64 encoding."""
    try:
        _require_knowledge_scope(owner_id, project_id)
        return file_store().save_text(
            owner_id=owner_id, project_id=project_id, filename=filename, content=content,
            media_type=media_type, description=description, tags=tags or [],
        )
    except (FileStoreError, SchedulerError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_files(
    owner_id: str | None = None,
    project_id: str | None = None,
    include_global: bool = True,
    tag: str | None = None,
    limit: int = 200,
):
    """Read-only. List stored-file metadata. File bytes are returned only by explicit read tools."""
    try:
        if project_id and not owner_id:
            raise FileStoreError("owner_id is required when project_id is provided")
        rows = file_store().list_files(
            owner_id=owner_id, project_id=project_id, include_global=include_global,
            tag=tag, limit=limit,
        )
        return {"ok": True, "count": len(rows), "files": rows}
    except FileStoreError as exc:
        return {"ok": False, "error": str(exc), "count": 0, "files": []}


@mcp.tool()
def get_file_info(file_id: str):
    """Read-only. Return metadata for one stored file without returning its content."""
    return _safe_call(file_store().get_info, file_id)


@mcp.tool()
def read_text_file(file_id: str, max_chars: int | None = None):
    """Read-only. Return UTF-8 text from a stored file, bounded by FILE_STORE_TEXT_MAX_CHARS."""
    return _safe_call(file_store().read_text, file_id, max_chars=max_chars)


@mcp.tool()
def get_file_base64(file_id: str):
    """Read-only. Return one stored file as base64 plus metadata."""
    return _safe_call(file_store().read_base64, file_id)


@mcp.tool()
def update_file_metadata(
    file_id: str,
    filename: str | None = None,
    media_type: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
):
    """WRITE ACTION. Update filename/media type/description/tags without changing stored bytes."""
    return _safe_call(
        file_store().update_metadata, file_id,
        filename=filename, media_type=media_type, description=description, tags=tags,
    )


@mcp.tool()
def delete_stored_file(file_id: str):
    """WRITE ACTION. Delete one stored-file record and remove its blob when no other record references it."""
    return _safe_call(file_store().delete, file_id)
'''
text = replace_once(text, "\n# Scheduler\n@mcp.tool()", file_tools + "\n\n# Scheduler\n@mcp.tool()", "insert file tools")

text = replace_once(
    text,
    'if tab in {"overview", "accounts", "amp", "tracking", "domains", "recipients", "knowledge", "scheduler"}:',
    'if tab in {"overview", "accounts", "amp", "tracking", "domains", "recipients", "knowledge", "files", "scheduler"}:',
    "redir files tab",
)
text = replace_once(
    text,
    "    knowledge_projects = _safe_call(scheduler().list_projects)\n    knowledge_owners = _safe_call(scheduler().list_owners)\n\n    tracking_stat",
    "    knowledge_projects = _safe_call(scheduler().list_projects)\n    knowledge_owners = _safe_call(scheduler().list_owners)\n    files_stat = _safe_call(file_store().status)\n    stored_files = _safe_call(file_store().list_files, limit=500)\n\n    tracking_stat",
    "dashboard file data",
)

file_rows_block = r'''

    file_rows = ""
    for stored in (stored_files if isinstance(stored_files, list) else []):
        fid = escape(str(stored.get("id", "")))
        filename = escape(str(stored.get("filename", "")))
        owner = escape(str(stored.get("owner_id", "")))
        project = escape(str(stored.get("project_id") or "global"))
        media_type = escape(str(stored.get("media_type", "application/octet-stream")))
        tags = escape(", ".join(stored.get("tags") or []))
        size = int(stored.get("size_bytes") or 0)
        description = escape(str(stored.get("description") or ""))
        file_rows += f"""<tr>
<td><strong>{filename}</strong><div class="small muted mono">{fid}</div><div class="small muted">{description}</div></td>
<td>{owner}<div class="small muted">{project}</div></td>
<td class="mono small">{media_type}<div class="muted">{size} bytes</div></td>
<td class="small">{tags}</td>
<td class="actions"><a href="/dashboard/files/{fid}/download"><button type="button">Download</button></a>
<form method="post" action="/dashboard/files/delete" onsubmit="return confirm('Delete this stored file?');">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}"><input type="hidden" name="file_id" value="{fid}"><button class="danger" type="submit">Delete</button></form></td></tr>"""
    file_count = len(stored_files) if isinstance(stored_files, list) else 0
    file_logical_bytes = int(files_stat.get("logical_bytes", 0)) if isinstance(files_stat, dict) else 0
    file_max_bytes = int(files_stat.get("max_bytes_per_file", 0)) if isinstance(files_stat, dict) else 0
    file_owner_selected = os.getenv("DEFAULT_OWNER_ID", "")
'''
text = replace_once(text, "\n    due_count = len(due) if isinstance(due, list) else 0", file_rows_block + "\n    due_count = len(due) if isinstance(due, list) else 0", "file rows")

text = replace_once(text, "· task registry · v9.0</p>", "· task registry + small files · v9.1</p>", "dashboard version")
text = replace_once(
    text,
    '  <a class="tab-link" href="#knowledge" data-tab="knowledge">Knowledge <span class="tab-count">{knowledge_total_count}</span></a>\n  <a class="tab-link" href="#scheduler"',
    '  <a class="tab-link" href="#knowledge" data-tab="knowledge">Knowledge <span class="tab-count">{knowledge_total_count}</span></a>\n  <a class="tab-link" href="#files" data-tab="files">Files <span class="tab-count">{file_count}</span></a>\n  <a class="tab-link" href="#scheduler"',
    "files tab link",
)
text = replace_once(
    text,
    '<div><strong>{knowledge_memory_count}</strong> memories · <strong>{knowledge_skill_count}</strong> skills</div>\n',
    '<div><strong>{knowledge_memory_count}</strong> memories · <strong>{knowledge_skill_count}</strong> skills</div>\n<div><strong>{file_count}</strong> stored files · <strong>{file_logical_bytes}</strong> logical bytes</div>\n',
    "overview file count",
)
text = replace_once(
    text,
    '<div class="small muted mono" style="margin-top:8px">build: {escape(os.getenv("BRIDGE_BUILD","unknown"))}</div>',
    '<div class="small muted mono" style="margin-top:8px">build: {escape(os.getenv("BRIDGE_BUILD") or os.getenv("POSTMASTER_REF") or "unknown")}</div>',
    "dashboard build fallback",
)

files_panel = r'''
<section class="tab-panel" id="panel-files" data-panel="files">
<div class="grid">
<section class="card">
<h2>Small-file store</h2>
<div><strong>{file_count}</strong> files · <strong>{file_logical_bytes}</strong> logical bytes</div>
<div class="small muted">Per-file limit: {file_max_bytes} bytes. Blobs are stored under /data by SHA-256; original filenames are metadata only.</div>
<p class="small muted">Files are private to this deployment, never executed by Postmaster, and WebGUI downloads use attachment disposition + nosniff.</p>
</section>
<section class="card wide">
<div class="panel-title"><h2>Upload file</h2><span class="small muted">owner/project scopes reuse the Tasks registry</span></div>
<form method="post" action="/dashboard/files/upload" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{escape(_csrf_value())}">
<div class="row">
<div class="field"><label>Owner</label><select name="owner_id" required>{_knowledge_owner_options(file_owner_selected)}</select></div>
<div class="field"><label>Project</label><select name="project_id">{_knowledge_project_options(None)}</select></div>
<div class="field grow"><label>File</label><input type="file" name="file" required></div>
</div>
<div class="row" style="margin-top:10px">
<div class="field grow"><label>Description</label><input type="text" name="description" placeholder="Optional note"></div>
<div class="field grow"><label>Tags (comma separated)</label><input type="text" name="tags" placeholder="docs, config, reference"></div>
<button class="primary" type="submit">Upload</button>
</div>
</form>
</section>
<section class="card wide">
<div class="panel-title"><h2>Stored files</h2><span class="badge">{file_count} total</span></div>
<div class="scroll"><table><thead><tr><th>File</th><th>Owner / project</th><th>Type / size</th><th>Tags</th><th></th></tr></thead>
<tbody>{file_rows or '<tr><td colspan="5" class="muted">No stored files yet</td></tr>'}</tbody></table></div>
</section>
</div>
</section>

'''
text = replace_once(text, '<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">', files_panel + '<section class="tab-panel" id="panel-scheduler" data-panel="scheduler">', "files panel")
text = replace_once(
    text,
    "const allowed = new Set(['overview','accounts','amp','tracking','domains','recipients','knowledge','scheduler']);",
    "const allowed = new Set(['overview','accounts','amp','tracking','domains','recipients','knowledge','files','scheduler']);",
    "files js tab",
)
text = replace_once(text, 'return _layout("Postmaster MCP v9.0", body, flash=flash)', 'return _layout("Postmaster MCP v9.1", body, flash=flash)', "layout version")

handlers = r'''

async def dashboard_file_upload(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    try:
        owner_id = str(form.get("owner_id", "")).strip()
        project_id = str(form.get("project_id", "")).strip() or None
        _require_knowledge_scope(owner_id, project_id)
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", "") or not hasattr(upload, "read"):
            raise FileStoreError("file upload is required")
        data = await upload.read(file_store().max_bytes + 1)
        tags = [x.strip() for x in str(form.get("tags", "")).split(",") if x.strip()]
        saved = file_store().save_bytes(
            owner_id=owner_id,
            project_id=project_id,
            filename=str(upload.filename),
            data=data,
            media_type=str(getattr(upload, "content_type", "") or "") or None,
            description=str(form.get("description", "")),
            tags=tags,
        )
        return _redir(f"File uploaded: {saved.get('filename')}", "files")
    except Exception as exc:
        logger.exception("File upload failed")
        return _redir(f"{type(exc).__name__}: {exc}", "files")


async def dashboard_file_delete(request: Request):
    form, error = await _verified_form(request)
    if error:
        return error
    result = _safe_call(file_store().delete, str(form.get("file_id", "")))
    return _redir("Stored file deleted" if result.get("ok") else result.get("error", "Failed"), "files")


async def dashboard_file_download(request: Request):
    try:
        info, data = file_store().raw_bytes(str(request.path_params.get("file_id", "")))
        filename = quote(str(info.get("filename") or "download.bin"), safe="")
        return Response(
            data,
            media_type=str(info.get("media_type") or "application/octet-stream"),
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store, max-age=0",
            },
        )
    except FileStoreError as exc:
        return PlainTextResponse(str(exc), status_code=404)
'''
text = replace_once(text, "\n\nasync def dashboard_account_save(request: Request):", handlers + "\n\nasync def dashboard_account_save(request: Request):", "file web handlers")

text = replace_once(
    text,
    "    analytics_store()\n    ctx = context_engine()",
    "    analytics_store()\n    file_store()\n    ctx = context_engine()",
    "lifespan file store",
)
text = replace_once(
    text,
    '        Route("/dashboard/knowledge/reindex", dashboard_knowledge_reindex, methods=["POST"]),\n',
    '        Route("/dashboard/knowledge/reindex", dashboard_knowledge_reindex, methods=["POST"]),\n        Route("/dashboard/files/upload", dashboard_file_upload, methods=["POST"]),\n        Route("/dashboard/files/delete", dashboard_file_delete, methods=["POST"]),\n        Route("/dashboard/files/{file_id}/download", dashboard_file_download, methods=["GET"]),\n',
    "file routes",
)
server_path.write_text(text)

compose = Path("postmaster-mcp.yml")
yaml = compose.read_text()
yaml = replace_once(
    yaml,
    "      RECIPIENT_POLICY_DB_PATH: /data/recipient-policy.db\n      MAIL_ACCOUNTS_DB_PATH:",
    "      RECIPIENT_POLICY_DB_PATH: /data/recipient-policy.db\n      FILE_STORE_DB_PATH: /data/files.db\n      FILE_STORE_ROOT: /data/files\n      FILE_STORE_MAX_BYTES: \"1048576\"\n      FILE_STORE_MAX_TOTAL_BYTES: \"104857600\"\n      FILE_STORE_MAX_FILES: \"1000\"\n      FILE_STORE_TEXT_MAX_CHARS: \"200000\"\n      MAIL_ACCOUNTS_DB_PATH:",
    "compose file store env",
)
compose.write_text(yaml)

readme = Path("README.md")
rd = readme.read_text()
rd += r'''

## v9.1 small-file store

v9.1 adds a private persistent store for small reference files. Metadata is kept in SQLite while file bytes are stored as SHA-256-addressed blobs under `/data/files`, so user-provided filenames never become filesystem paths. The default public stack limits individual files to 1 MiB, the logical store to 100 MiB and 1000 records; hard application caps prevent accidentally configuring unbounded values.

MCP clients can save UTF-8 text directly or binary data as base64, list scoped metadata, read text with a character budget, retrieve binary content as base64, update metadata and delete files. Owner/project scopes reuse the scheduler registry. The WebGUI has a Files tab for upload, download and deletion. Downloads are forced as attachments with `X-Content-Type-Options: nosniff`; Postmaster never executes stored content and does not expose public file URLs.

The file store is intentionally separate from Knowledge in v9.1. Uploading a document does not automatically inject it into semantic context; a later version can add explicit opt-in document extraction/indexing without making arbitrary uploads part of prompts by default.
'''
readme.write_text(rd)

ci = Path(".github/workflows/v9-runtime-tests.yml")
ct = ci.read_text()
ct = replace_once(
    ct,
    "          RECIPIENT_POLICY_DB_PATH: /tmp/postmaster-ci-recipient-policy.db\n",
    "          RECIPIENT_POLICY_DB_PATH: /tmp/postmaster-ci-recipient-policy.db\n          FILE_STORE_DB_PATH: /tmp/postmaster-ci-files.db\n          FILE_STORE_ROOT: /tmp/postmaster-ci-files\n",
    "ci file env",
)
ct = replace_once(
    ct,
    "          assert env['CONTEXT_MODEL_DIMS'] == '128'\n",
    "          assert env['CONTEXT_MODEL_DIMS'] == '128'\n          assert env['FILE_STORE_DB_PATH'] == '/data/files.db'\n          assert env['FILE_STORE_ROOT'] == '/data/files'\n          assert env['FILE_STORE_MAX_BYTES'] == '1048576'\n",
    "ci yaml file assertions",
)
ci.write_text(ct)
