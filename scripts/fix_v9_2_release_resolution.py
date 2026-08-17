from pathlib import Path

path = Path('postmaster-mcp.yml')
text = path.read_text()
old = '''        request = urllib.request.Request(\n            f"https://api.github.com/repos/{repo}/releases/latest",\n            headers={\n                "Accept": "application/vnd.github+json",\n                "User-Agent": "Postmaster-MCP-bootstrap",\n            },\n        )\n        with urllib.request.urlopen(request, timeout=30) as response:\n            payload = json.load(response)\n        tag = str(payload.get("tag_name") or "").strip()\n        if not re.fullmatch(r"v[0-9]+\\.[0-9]+\\.[0-9]+", tag):\n            raise RuntimeError(f"Latest stable release has unexpected tag: {tag!r}")\n        print(tag)\n'''
new = '''        request = urllib.request.Request(\n            f"https://api.github.com/repos/{repo}/releases?per_page=100",\n            headers={\n                "Accept": "application/vnd.github+json",\n                "User-Agent": "Postmaster-MCP-bootstrap",\n            },\n        )\n        with urllib.request.urlopen(request, timeout=30) as response:\n            payload = json.load(response)\n        candidates = []\n        for release in payload if isinstance(payload, list) else []:\n            if release.get("draft") or release.get("prerelease"):\n                continue\n            tag = str(release.get("tag_name") or "").strip()\n            match = re.fullmatch(r"v([0-9]+)\\.([0-9]+)\\.([0-9]+)", tag)\n            if match:\n                candidates.append((tuple(int(x) for x in match.groups()), tag))\n        if not candidates:\n            raise RuntimeError("No stable Postmaster vX.Y.Z release found")\n        print(max(candidates, key=lambda item: item[0])[1])\n'''
if text.count(old) != 1:
    raise RuntimeError(f'expected one release resolver block, found {text.count(old)}')
path.write_text(text.replace(old, new, 1))
print('release resolver hardened')
