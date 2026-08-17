from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
old = ROOT / "src/postmaster/hostinger_mail.py"
new = ROOT / "src/postmaster/mail_bridge.py"
if old.exists():
    old.rename(new)

replacements = [
    ("EnhancedHostingerMailClient", "EnhancedMailClient"),
    ("HostingerMailClient", "MailClient"),
    ("hostinger_mail", "mail_bridge"),
    ("hostinger-mcp", "postmaster-mcp"),
    ("HOSTINGER_EMAIL", "MAIL_EMAIL"),
    ("HOSTINGER_PASSWORD", "MAIL_PASSWORD"),
    ("imap.hostinger.com", "imap.example.com"),
    ("smtp.hostinger.com", "smtp.example.com"),
    ("Hostinger", "mail provider"),
    ("hostinger", "provider"),
]

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if rel.parts[:2] == (".github", "workflows"):
        continue
    if rel == Path("scripts/provider_generic_refactor.py"):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    updated = text
    for before, after in replacements:
        updated = updated.replace(before, after)
    if updated != text:
        path.write_text(updated, encoding="utf-8")

# Generic public defaults must be obvious and non-routable examples.
compose = ROOT / "postmaster-mcp.yml"
text = compose.read_text(encoding="utf-8")
assert "services:\n  postmaster-mcp:" in text
assert "container_name: postmaster-mcp" in text
assert 'MAIL_EMAIL: ""' in text
assert 'MAIL_PASSWORD: ""' in text
assert "IMAP_HOST: imap.example.com" in text
assert "SMTP_HOST: smtp.example.com" in text
compose.write_text(text, encoding="utf-8")

# Current public tree (apart from this one-shot script/workflow) must not carry provider branding.
needle = "host" + "inger"
residual = []
for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if rel.parts[:2] == (".github", "workflows") or rel == Path("scripts/provider_generic_refactor.py"):
        continue
    try:
        data = path.read_text(encoding="utf-8").casefold()
    except (UnicodeDecodeError, OSError):
        continue
    if needle in data:
        residual.append(str(rel))
if residual:
    raise SystemExit(f"provider-specific residuals: {residual}")
