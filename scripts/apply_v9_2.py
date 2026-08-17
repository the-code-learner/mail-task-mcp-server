from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1))


# server.py
replace_once(
    "src/postmaster/server.py",
    "from functools import lru_cache\nfrom html import escape\nfrom typing import Any\n",
    "from functools import lru_cache\nfrom html import escape\nfrom pathlib import Path\nfrom typing import Any\n",
    "path import",
)
replace_once(
    "src/postmaster/server.py",
    "from .file_store import FileStore, FileStoreError\n",
    "from .file_store import FileStore, FileStoreError\nfrom .remote_file import (\n    OpenAIFile, RemoteFileError, download_openai_file, filename_for_openai_file,\n    remote_max_batch_files,\n)\n",
    "remote file import",
)
replace_once(
    "src/postmaster/server.py",
    '        "v9.1 adds persistent project memory/skills and a scoped small-file store, revision history, FTS5 and optional Model2Vec hybrid retrieval. "\n',
    '        "v9.2 adds native ChatGPT file inputs on top of persistent project memory/skills, the scoped small-file store, revision history, FTS5 and optional Model2Vec hybrid retrieval. "\n',
    "instructions version",
)
replace_once(
    "src/postmaster/server.py",
    "def file_store() -> FileStore:\n    return FileStore()\n\ndef _safe_call(fn, *args, **kwargs):\n",
    "def file_store() -> FileStore:\n    return FileStore()\n\n\ndef _project_version() -> str:\n    try:\n        return (Path(__file__).resolve().parents[2] / \"VERSION\").read_text(encoding=\"utf-8\").strip() or \"unknown\"\n    except OSError:\n        return \"unknown\"\n\n\ndef _safe_call(fn, *args, **kwargs):\n",
    "version helper",
)
replace_once(
    "src/postmaster/server.py",
    "    except (MailBridgeError, SchedulerError, AccountStoreError, AnalyticsError, KnowledgeError, SemanticError, FileStoreError) as exc:\n",
    "    except (MailBridgeError, SchedulerError, AccountStoreError, AnalyticsError, KnowledgeError, SemanticError, FileStoreError, RemoteFileError) as exc:\n",
    "safe call errors",
)
old_build = '''@mcp.tool()\ndef build_status():\n    \"\"\"Read-only. Return the running bridge build and high-level v9.1 capabilities.\"\"\"\n    return {\n        \"ok\": True,\n        \"build\": os.getenv(\"BRIDGE_BUILD\") or os.getenv(\"POSTMASTER_REF\") or \"unknown\",\n        \"multi_account\": True,\n        \"amp_per_account\": True,\n        \"per_recipient_open_tracking\": True,\n        \"reply_open_tracking\": True,\n        \"tracking_default_applies_to_replies\": True,\n        \"visible_to_cc_preserved_for_tracked_fanout\": True,\n        \"scheduler_mode\": \"task_registry_only\",\n        \"persistent_context\": True,\n        \"fts5_search\": True,\n        \"optional_model2vec\": True,\n        \"small_file_store\": True,\n    }\n'''
new_build = '''@mcp.tool()\ndef build_status():\n    \"\"\"Read-only. Return the running build, release version and high-level v9.2 capabilities.\"\"\"\n    resolved = os.getenv(\"BRIDGE_BUILD\") or os.getenv(\"POSTMASTER_REF\") or \"unknown\"\n    return {\n        \"ok\": True,\n        \"version\": _project_version(),\n        \"build\": resolved,\n        \"requested_version\": os.getenv(\"POSTMASTER_VERSION\") or os.getenv(\"POSTMASTER_REQUESTED_VERSION\") or resolved,\n        \"multi_account\": True,\n        \"amp_per_account\": True,\n        \"per_recipient_open_tracking\": True,\n        \"reply_open_tracking\": True,\n        \"tracking_default_applies_to_replies\": True,\n        \"visible_to_cc_preserved_for_tracked_fanout\": True,\n        \"scheduler_mode\": \"task_registry_only\",\n        \"persistent_context\": True,\n        \"fts5_search\": True,\n        \"optional_model2vec\": True,\n        \"small_file_store\": True,\n        \"native_chatgpt_file_upload\": True,\n    }\n'''
replace_once("src/postmaster/server.py", old_build, new_build, "build status")
replace_once(
    "src/postmaster/server.py",
    '            "build": os.getenv("BRIDGE_BUILD", "unknown"),\n',
    '            "build": os.getenv("BRIDGE_BUILD") or os.getenv("POSTMASTER_REF") or "unknown",\n',
    "mailbox build fallback",
)

native_tools = r'''

def _save_uploaded_file_impl(
    *,
    owner_id: str,
    file: OpenAIFile,
    project_id: str | None = None,
    filename: str | None = None,
    media_type: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
):
    _require_knowledge_scope(owner_id, project_id)
    store = file_store()
    downloaded = download_openai_file(file, max_bytes=store.max_bytes)
    resolved_name = filename or filename_for_openai_file(file)
    resolved_media = media_type or file.get("mime_type") or downloaded.response_media_type
    return store.save_bytes(
        owner_id=owner_id,
        project_id=project_id,
        filename=resolved_name,
        data=downloaded.data,
        media_type=resolved_media,
        description=description,
        tags=tags or [],
    )


@mcp.tool(meta={
    "openai/fileParams": ["file"],
    "openai/toolInvocation/invoking": "Saving uploaded file",
    "openai/toolInvocation/invoked": "Uploaded file saved",
})
def save_uploaded_file(
    owner_id: str,
    file: OpenAIFile,
    project_id: str | None = None,
    filename: str | None = None,
    media_type: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
):
    """WRITE ACTION. Save one file attached/authorized by ChatGPT without routing Base64 through model context."""
    try:
        return _save_uploaded_file_impl(
            owner_id=owner_id,
            file=file,
            project_id=project_id,
            filename=filename,
            media_type=media_type,
            description=description,
            tags=tags,
        )
    except (FileStoreError, SchedulerError, RemoteFileError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(meta={
    "openai/fileParams": ["files"],
    "openai/toolInvocation/invoking": "Saving uploaded files",
    "openai/toolInvocation/invoked": "Uploaded files processed",
})
def save_uploaded_files(
    owner_id: str,
    files: list[OpenAIFile],
    project_id: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
):
    """WRITE ACTION. Save several ChatGPT file inputs. Returns per-file results and preserves successful items if another fails."""
    if not files:
        return {"ok": False, "error": "files must contain at least one ChatGPT file object", "saved": [], "errors": []}
    maximum = remote_max_batch_files()
    if len(files) > maximum:
        return {"ok": False, "error": f"batch exceeds FILE_STORE_REMOTE_MAX_BATCH_FILES ({maximum})", "saved": [], "errors": []}
    saved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, source in enumerate(files):
        try:
            saved.append(_save_uploaded_file_impl(
                owner_id=owner_id,
                file=source,
                project_id=project_id,
                description=description,
                tags=tags,
            ))
        except (FileStoreError, SchedulerError, RemoteFileError) as exc:
            errors.append({"index": index, "file_id": str(source.get("file_id") or ""), "error": str(exc)})
    return {
        "ok": not errors,
        "partial": bool(saved) and bool(errors),
        "saved_count": len(saved),
        "error_count": len(errors),
        "saved": saved,
        "errors": errors,
    }
'''
replace_once(
    "src/postmaster/server.py",
    "\n\n@mcp.tool()\ndef save_text_file(\n",
    native_tools + "\n\n@mcp.tool()\ndef save_text_file(\n",
    "native upload tools",
)

# postmaster-mcp.yml
replace_once(
    "postmaster-mcp.yml",
    '''      # Bootstrap. Pin an immutable tag/commit for production.\n      POSTMASTER_REPO: the-code-learner/mail-task-mcp-server\n      POSTMASTER_REF: main\n      POSTMASTER_REFRESH_ON_START: "true"\n''',
    '''      # Bootstrap update policy. `latest` follows stable GitHub Releases; an explicit version/tag/commit stays pinned.\n      POSTMASTER_REPO: the-code-learner/mail-task-mcp-server\n      POSTMASTER_VERSION: latest\n      POSTMASTER_FORCE_REFRESH: "false"\n''',
    "bootstrap environment",
)
replace_once(
    "postmaster-mcp.yml",
    '''      FILE_STORE_TEXT_MAX_CHARS: "200000"\n''',
    '''      FILE_STORE_TEXT_MAX_CHARS: "200000"\n      FILE_STORE_REMOTE_TIMEOUT_SECONDS: "30"\n      FILE_STORE_REMOTE_MAX_REDIRECTS: "3"\n      FILE_STORE_REMOTE_MAX_BATCH_FILES: "20"\n''',
    "remote file settings",
)
old_bootstrap_head = '''        APP_ROOT=/opt/postmaster\n        REPO="$${POSTMASTER_REPO:-the-code-learner/mail-task-mcp-server}"\n        REF="$${POSTMASTER_REF:-main}"\n        REF_KEY="$$(printf '%s' "$$REF" | tr '/:@ ' '____')"\n        TARGET="$$APP_ROOT/releases/$$REF_KEY"\n        CURRENT="$$APP_ROOT/current"\n        REFRESH="$${POSTMASTER_REFRESH_ON_START:-false}"\n        mkdir -p "$$APP_ROOT/releases"\n\n        if [ "$$REFRESH" = "true" ] || [ ! -f "$$TARGET/.postmaster-source-ready" ]; then\n'''
new_bootstrap_head = '''        APP_ROOT=/opt/postmaster\n        REPO="$${POSTMASTER_REPO:-the-code-learner/mail-task-mcp-server}"\n        REQUESTED="$${POSTMASTER_VERSION:-$${POSTMASTER_REF:-latest}}"\n        FORCE_REFRESH="$${POSTMASTER_FORCE_REFRESH:-$${POSTMASTER_REFRESH_ON_START:-false}}"\n        CURRENT="$$APP_ROOT/current"\n        mkdir -p "$$APP_ROOT/releases"\n\n        case "$$REQUESTED" in\n          [0-9]*.[0-9]*.[0-9]*) REQUESTED="v$$REQUESTED" ;;\n        esac\n\n        if [ "$$REQUESTED" = "latest" ]; then\n          export REPO\n          if REF="$$(python - <<'PY'\n        import json\n        import os\n        import re\n        import urllib.request\n\n        repo = os.environ["REPO"]\n        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):\n            raise RuntimeError("Invalid POSTMASTER_REPO")\n        request = urllib.request.Request(\n            f"https://api.github.com/repos/{repo}/releases/latest",\n            headers={\n                "Accept": "application/vnd.github+json",\n                "User-Agent": "Postmaster-MCP-bootstrap",\n            },\n        )\n        with urllib.request.urlopen(request, timeout=30) as response:\n            payload = json.load(response)\n        tag = str(payload.get("tag_name") or "").strip()\n        if not re.fullmatch(r"v[0-9]+\\.[0-9]+\\.[0-9]+", tag):\n            raise RuntimeError(f"Latest stable release has unexpected tag: {tag!r}")\n        print(tag)\n        PY\n          )"; then\n            :\n          elif [ -x "$$CURRENT/scripts/start.sh" ]; then\n            echo "WARNING: could not resolve latest release; using cached current release." >&2\n            exec "$$CURRENT/scripts/start.sh"\n          else\n            echo "ERROR: could not resolve latest release and no cached release exists." >&2\n            exit 1\n          fi\n        else\n          REF="$$REQUESTED"\n        fi\n\n        export POSTMASTER_REQUESTED_VERSION="$$REQUESTED"\n        export POSTMASTER_REF="$$REF"\n        REF_KEY="$$(printf '%s' "$$REF" | tr '/:@ ' '____')"\n        TARGET="$$APP_ROOT/releases/$$REF_KEY"\n\n        if [ "$$FORCE_REFRESH" = "true" ] || [ ! -f "$$TARGET/.postmaster-source-ready" ]; then\n'''
replace_once("postmaster-mcp.yml", old_bootstrap_head, new_bootstrap_head, "bootstrap resolution")
replace_once(
    "postmaster-mcp.yml",
    '''            if [ ! -f "$$TARGET/.postmaster-source-ready" ]; then\n              echo "ERROR: source download failed and no cached release exists." >&2\n              exit 1\n            fi\n            echo "WARNING: source refresh failed; using cached $$TARGET" >&2\n''',
    '''            if [ ! -f "$$TARGET/.postmaster-source-ready" ]; then\n              if [ -x "$$CURRENT/scripts/start.sh" ]; then\n                echo "WARNING: requested release download failed; using cached current release." >&2\n                exec "$$CURRENT/scripts/start.sh"\n              fi\n              echo "ERROR: source download failed and no cached release exists." >&2\n              exit 1\n            fi\n            echo "WARNING: source refresh failed; using cached $$TARGET" >&2\n''',
    "bootstrap cache fallback",
)

# README.md
replace_once(
    "README.md",
    "Version 9 moves the project from the old all-in-one Compose source layout to a normal, maintainable multi-file application **without giving up one-file deployment**: Portainer still needs only `postmaster-mcp.yml`.\n",
    "Version 9 moves the project from the old all-in-one Compose source layout to a normal, maintainable multi-file application **without giving up one-file deployment**: Portainer still needs only `postmaster-mcp.yml`. From v9.2 the same YAML can follow stable releases automatically or stay pinned to an exact version.\n",
    "README intro",
)
replace_once(
    "README.md",
    "- CI coverage for the bootstrap, MIME parser, knowledge store and semantic-model provisioning.\n",
    "- CI coverage for the bootstrap, MIME parser, knowledge store and semantic-model provisioning;\n- persistent small-file storage plus native ChatGPT file inputs in v9.2;\n- semantic release history through `VERSION`, `CHANGELOG.md` and immutable `vX.Y.Z` release tags.\n",
    "README changes list",
)
old_revision = '''## 2. Choose the source revision\n\nThe stack downloads the application from GitHub using:\n\n```yaml\nPOSTMASTER_REPO: the-code-learner/mail-task-mcp-server\nPOSTMASTER_REF: v9-structural-runtime\n```\n\nA mutable branch is convenient while testing. For production, pin `POSTMASTER_REF` to an immutable release tag or commit SHA.\n'''
new_revision = '''## 2. Choose the update policy\n\nThe v9.2 bootstrap uses one persistent YAML and a version policy:\n\n```yaml\nPOSTMASTER_REPO: the-code-learner/mail-task-mcp-server\nPOSTMASTER_VERSION: latest\n```\n\n`latest` resolves the newest stable GitHub Release at container startup and only downloads it when that release is not already cached. To freeze a deployment, use an exact release such as `v9.2.0` (or `9.2.0`), or an immutable commit SHA. Existing deployments that still provide only `POSTMASTER_REF` remain supported as a compatibility fallback.\n\nIf GitHub is temporarily unavailable, a previously working cached release is kept and started instead of replacing it with an incomplete update. Set `POSTMASTER_FORCE_REFRESH=true` only when you deliberately want to redownload the already selected revision.\n'''
replace_once("README.md", old_revision, new_revision, "README update policy")
replace_once(
    "README.md",
    "    semantic_engine.py\n",
    "    semantic_engine.py\n    file_store.py\n    remote_file.py\n",
    "README source tree modules",
)
replace_once(
    "README.md",
    "requirements.txt\npostmaster-mcp.yml\n",
    "requirements.txt\nVERSION\nCHANGELOG.md\npostmaster-mcp.yml\n",
    "README source tree root files",
)

versioning_section = '''\n---\n\n# Versioning and updates\n\nStable Postmaster releases use Semantic Versioning and are recorded in `CHANGELOG.md`. The repository `VERSION` file contains the application version, while GitHub release tags use `vX.Y.Z`.\n\nFor a Portainer deployment:\n\n```text\nPOSTMASTER_VERSION=latest   -> follow the latest stable GitHub Release on restart\nPOSTMASTER_VERSION=v9.2.0  -> stay pinned to that exact release\nPOSTMASTER_VERSION=<SHA>   -> stay pinned to an immutable commit\n```\n\n`build_status` reports the application `version`, the resolved running `build`, and the `requested_version` policy so an MCP client can distinguish `latest` from the concrete release actually running.\n\n# Native ChatGPT file upload (v9.2)\n\nThe portable MCP `save_file(content_base64=...)` tool remains available. ChatGPT clients can instead use `save_uploaded_file` or `save_uploaded_files`; those tools declare `_meta["openai/fileParams"]`, so ChatGPT passes temporary authorized file download objects rather than forcing large Base64 strings through model context.\n\nRemote downloads are HTTPS-only, bounded by the same per-file store limit while streaming, limited in redirects and timeout, checked against non-public address resolution, and then stored through the same SHA-256 content-addressed `FileStore`. Uploaded content is never executed or automatically added to semantic Knowledge.\n'''
readme = Path("README.md")
text = readme.read_text()
if "# Versioning and updates" in text:
    raise RuntimeError("README versioning section already present")
readme.write_text(text + versioning_section)

print("v9.2 transformation complete")
