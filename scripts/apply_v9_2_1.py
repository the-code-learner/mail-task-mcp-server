from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# --- server.py ---
server_path = Path("src/postmaster/server.py")
server = server_path.read_text(encoding="utf-8")
server = replace_once(
    server,
    '    """Read-only. Return the running build, release version and high-level v9.2 capabilities."""\n',
    '    """Read-only. Return the running build, release version and high-level v9.2.1 capabilities."""\n',
    "build_status docstring",
)
server = replace_once(
    server,
    '        "requested_version": os.getenv("POSTMASTER_VERSION") or os.getenv("POSTMASTER_REQUESTED_VERSION") or resolved,\n',
    '        "requested_version": os.getenv("POSTMASTER_VERSION") or os.getenv("POSTMASTER_REQUESTED_VERSION") or resolved,\n'
    '        "check_updates_on_start": os.getenv("POSTMASTER_CHECK_UPDATES_ON_START", "true").strip().lower() == "true",\n'
    '        "force_refresh": os.getenv("POSTMASTER_FORCE_REFRESH", "false").strip().lower() == "true",\n',
    "build_status update policy fields",
)
server_path.write_text(server, encoding="utf-8")


# --- postmaster-mcp.yml ---
yaml_path = Path("postmaster-mcp.yml")
yml = yaml_path.read_text(encoding="utf-8")
yml = replace_once(
    yml,
    '      POSTMASTER_VERSION: latest\n      POSTMASTER_FORCE_REFRESH: "false"\n',
    '      POSTMASTER_VERSION: latest\n'
    '      POSTMASTER_CHECK_UPDATES_ON_START: "true"\n'
    '      POSTMASTER_FORCE_REFRESH: "false"\n',
    "bootstrap env insertion",
)
yml = replace_once(
    yml,
    '        REQUESTED="$${POSTMASTER_VERSION:-$${POSTMASTER_REF:-latest}}"\n'
    '        FORCE_REFRESH="$${POSTMASTER_FORCE_REFRESH:-$${POSTMASTER_REFRESH_ON_START:-false}}"\n'
    '        CURRENT="$$APP_ROOT/current"\n'
    '        mkdir -p "$$APP_ROOT/releases"\n\n'
    '        case "$$REQUESTED" in\n'
    '          [0-9]*.[0-9]*.[0-9]*) REQUESTED="v$$REQUESTED" ;;\n'
    '        esac\n\n',
    '        REQUESTED="$${POSTMASTER_VERSION:-$${POSTMASTER_REF:-latest}}"\n'
    '        CHECK_UPDATES="$${POSTMASTER_CHECK_UPDATES_ON_START:-true}"\n'
    '        FORCE_REFRESH="$${POSTMASTER_FORCE_REFRESH:-$${POSTMASTER_REFRESH_ON_START:-false}}"\n'
    '        CURRENT="$$APP_ROOT/current"\n'
    '        mkdir -p "$$APP_ROOT/releases"\n\n'
    '        case "$$CHECK_UPDATES" in\n'
    '          true|false) ;;\n'
    '          *) echo "ERROR: POSTMASTER_CHECK_UPDATES_ON_START must be true or false." >&2; exit 2 ;;\n'
    '        esac\n\n'
    '        case "$$REQUESTED" in\n'
    '          [0-9]*.[0-9]*.[0-9]*) REQUESTED="v$$REQUESTED" ;;\n'
    '        esac\n\n',
    "bootstrap check-updates variables",
)
old_latest = '''        if [ "$$REQUESTED" = "latest" ]; then
          export REPO
          if REF="$$(python - <<'PY'
        import json
        import os
        import re
        import urllib.request

        repo = os.environ["REPO"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("Invalid POSTMASTER_REPO")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Postmaster-MCP-bootstrap",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        candidates = []
        for release in payload if isinstance(payload, list) else []:
            if release.get("draft") or release.get("prerelease"):
                continue
            tag = str(release.get("tag_name") or "").strip()
            match = re.fullmatch(r"v([0-9]+)\\.([0-9]+)\\.([0-9]+)", tag)
            if match:
                candidates.append((tuple(int(x) for x in match.groups()), tag))
        if not candidates:
            raise RuntimeError("No stable Postmaster vX.Y.Z release found")
        print(max(candidates, key=lambda item: item[0])[1])
        PY
          )"; then
            :
          elif [ -x "$$CURRENT/scripts/start.sh" ]; then
            echo "WARNING: could not resolve latest release; using cached current release." >&2
            exec "$$CURRENT/scripts/start.sh"
          else
            echo "ERROR: could not resolve latest release and no cached release exists." >&2
            exit 1
          fi
        else
          REF="$$REQUESTED"
        fi

        export POSTMASTER_REQUESTED_VERSION="$$REQUESTED"
        export POSTMASTER_REF="$$REF"
'''
new_latest = '''        REF=""
        if [ "$$REQUESTED" = "latest" ]; then
          if [ "$$CHECK_UPDATES" = "false" ] && [ -x "$$CURRENT/scripts/start.sh" ] && [ -s "$$CURRENT/.postmaster-source-ready" ]; then
            REF="$$(sed -n '1p' "$$CURRENT/.postmaster-source-ready" | tr -d '\\r\\n')"
            if [ -n "$$REF" ]; then
              echo "Postmaster update check disabled; using cached current source: $$REF"
            fi
          fi

          if [ -z "$$REF" ]; then
            export REPO
            if REF="$$(python - <<'PY'
        import json
        import os
        import re
        import urllib.request

        repo = os.environ["REPO"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RuntimeError("Invalid POSTMASTER_REPO")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Postmaster-MCP-bootstrap",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        candidates = []
        for release in payload if isinstance(payload, list) else []:
            if release.get("draft") or release.get("prerelease"):
                continue
            tag = str(release.get("tag_name") or "").strip()
            match = re.fullmatch(r"v([0-9]+)\\.([0-9]+)\\.([0-9]+)", tag)
            if match:
                candidates.append((tuple(int(x) for x in match.groups()), tag))
        if not candidates:
            raise RuntimeError("No stable Postmaster vX.Y.Z release found")
        print(max(candidates, key=lambda item: item[0])[1])
        PY
            )"; then
              :
            elif [ -x "$$CURRENT/scripts/start.sh" ]; then
              if [ -s "$$CURRENT/.postmaster-source-ready" ]; then
                CACHED_REF="$$(sed -n '1p' "$$CURRENT/.postmaster-source-ready" | tr -d '\\r\\n')"
                if [ -n "$$CACHED_REF" ]; then
                  export POSTMASTER_REF="$$CACHED_REF"
                fi
              fi
              export POSTMASTER_REQUESTED_VERSION="$$REQUESTED"
              echo "WARNING: could not resolve latest release; using cached current release." >&2
              exec "$$CURRENT/scripts/start.sh"
            else
              echo "ERROR: could not resolve latest release and no cached release exists." >&2
              exit 1
            fi
          fi
        else
          REF="$$REQUESTED"
        fi

        export POSTMASTER_REQUESTED_VERSION="$$REQUESTED"
        export POSTMASTER_REF="$$REF"
'''
yml = replace_once(yml, old_latest, new_latest, "latest bootstrap policy")
yaml_path.write_text(yml, encoding="utf-8")


# --- README.md ---
readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
old_readme = '''```yaml
POSTMASTER_REPO: the-code-learner/mail-task-mcp-server
POSTMASTER_VERSION: latest
```

`latest` resolves the newest stable GitHub Release at container startup and only downloads it when that release is not already cached. To freeze a deployment, use an exact release such as `v9.2.0` (or `9.2.0`), or an immutable commit SHA. Existing deployments that still provide only `POSTMASTER_REF` remain supported as a compatibility fallback.

If GitHub is temporarily unavailable, a previously working cached release is kept and started instead of replacing it with an incomplete update. Set `POSTMASTER_FORCE_REFRESH=true` only when you deliberately want to redownload the already selected revision.
'''
new_readme = '''```yaml
POSTMASTER_REPO: the-code-learner/mail-task-mcp-server
POSTMASTER_VERSION: latest
POSTMASTER_CHECK_UPDATES_ON_START: "true"
POSTMASTER_FORCE_REFRESH: "false"
```

`latest` follows the newest stable `vX.Y.Z` GitHub Release. With `POSTMASTER_CHECK_UPDATES_ON_START=true` (the default), Postmaster resolves the newest stable application release at every container start and only downloads it when that release is not already cached. Set `POSTMASTER_CHECK_UPDATES_ON_START=false` to keep using the currently cached source without contacting GitHub for an update check; if no usable cached source exists yet, Postmaster resolves `latest` once so the first boot can succeed.

To freeze a deployment independently of the update-check switch, use an exact release such as `v9.2.1` (or `9.2.1`), or an immutable commit SHA. Explicit versions never require a latest-release lookup. Existing deployments that still provide only `POSTMASTER_REF` remain supported as a compatibility fallback.

If GitHub is temporarily unavailable during an enabled update check, a previously working cached release is kept and started instead of replacing it with an incomplete update. `POSTMASTER_FORCE_REFRESH=true` is separate: it deliberately redownloads the already selected revision and may therefore use the network even when update checking is disabled.
'''
readme = replace_once(readme, old_readme, new_readme, "README update policy")
readme_path.write_text(readme, encoding="utf-8")

print("v9.2.1 source transformation complete")
