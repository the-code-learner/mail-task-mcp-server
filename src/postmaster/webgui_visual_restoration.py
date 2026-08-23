from __future__ import annotations

from html import escape
from typing import Any


# Presentation-only forward port of the pre-v9.6.2 WebGUI identity. The lazy shell,
# fragment lifecycle, routes and renderers remain owned by webgui_v962 and later overlays.
NAV_GROUPS = (
    (
        "Operate",
        (
            ("overview", "Dashboard", "⌂"),
            ("accounts", "Accounts", "◎"),
            ("mail-health", "Mail Health", "♡"),
            ("inbox", "Inbox", "▱"),
            ("compose", "Compose", "↗"),
            ("tracking", "Tracking", "⌾"),
            ("deliveries", "Deliveries", "➤"),
            ("suppressions", "Suppressions", "⊘"),
        ),
    ),
    (
        "Organize",
        (
            ("projects", "Projects", "◫"),
            ("scheduler", "Tasks", "☑"),
            ("knowledge", "Knowledge", "▤"),
            ("files", "Files", "▣"),
        ),
    ),
    (
        "Control",
        (
            ("security", "Security", "⌑"),
            ("amp", "AMP", "⚡"),
            ("system", "System", "⚙"),
            ("coverage", "MCP Coverage", "90"),
        ),
    ),
)


RESTORED_STYLE = r'''
/* webgui-pre-v962-color-restoration */
:root{
  /* v9.6.3 visual restoration used the old token names. Keep aliases so its
     existing component rules resolve against the v9.6.2 lazy shell. */
  --surface:var(--card);--border:var(--line);
}
.shell{grid-template-columns:238px minmax(0,1fr)}
.v962-nav{display:flex;flex-direction:column;flex-wrap:nowrap;gap:3px;padding:14px 12px;background:linear-gradient(180deg,#151a21 0%,#10141a 64%,#0c1015 100%);box-shadow:10px 0 34px rgba(0,0,0,.16)}
.v962-brand{padding:7px 9px 15px;border-bottom:1px solid var(--line);margin-bottom:4px}
.v962-brand strong{font-size:17px;letter-spacing:.01em}.v962-brand small{color:var(--muted)}
.v962-nav-label{padding:12px 9px 4px;color:var(--muted);font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.09em}
.v962-nav a.v962-nav-link{display:flex;align-items:center;gap:7px;width:100%;margin:0;border:1px solid transparent;background:transparent;border-radius:9px;padding:8px 9px;color:var(--muted)}
.v962-nav a.v962-nav-link:hover{color:var(--text);background:rgba(104,160,255,.07);border-color:rgba(104,160,255,.18)}
.v962-nav a.v962-nav-link.active{background:linear-gradient(135deg,color-mix(in srgb,var(--v963-accent-strong,var(--accent)) 27%,var(--card)),color-mix(in srgb,var(--v963-violet,#8b5cf6) 18%,var(--card)));border-color:color-mix(in srgb,var(--v963-accent,var(--accent)) 58%,var(--line));color:var(--text);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--v963-accent,var(--accent)) 13%,transparent)}
.v962-ico{width:24px;min-width:24px;text-align:center;color:color-mix(in srgb,var(--v963-accent,var(--accent)) 82%,var(--text));font-weight:800}
.v962-nav a.active .v962-ico{color:#fff}
.v962-legacy-links{margin-top:auto;border-top:1px solid var(--line);padding:10px 8px;display:grid;gap:6px;font-size:11px}
.v962-legacy-links a{color:var(--muted);text-decoration:none}.v962-legacy-links a:hover,.v962-legacy-links a.active{color:var(--text)}

/* Restore the project accents previously injected by webgui_helpers.decorate_styles().
   v9.6.2 kept emitting these classes but its standalone shell bypassed that decorator. */
.project-key{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin:8px 0}
.project-name{display:inline-block;padding:5px 8px;border-radius:8px;font-weight:750;line-height:1.25;border:1px solid transparent;white-space:normal}
.project-scope{display:inline-flex;align-items:center;padding:3px 7px;border-radius:999px;font-size:11px;font-weight:700;border:1px solid transparent}
.project-color-0{color:#93c5fd;background:rgba(37,99,235,.16);border-color:rgba(96,165,250,.36)}
.project-color-1{color:#d8b4fe;background:rgba(126,34,206,.16);border-color:rgba(192,132,252,.36)}
.project-color-2{color:#99f6e4;background:rgba(13,148,136,.16);border-color:rgba(94,234,212,.36)}
.project-color-3{color:#fde68a;background:rgba(202,138,4,.16);border-color:rgba(250,204,21,.36)}
.project-color-4{color:#fda4af;background:rgba(225,29,72,.16);border-color:rgba(251,113,133,.36)}
.project-color-5{color:#a5f3fc;background:rgba(8,145,178,.16);border-color:rgba(103,232,249,.36)}
.project-color-6{color:#bef264;background:rgba(101,163,13,.16);border-color:rgba(163,230,53,.36)}
.project-color-7{color:#fdba74;background:rgba(234,88,12,.16);border-color:rgba(251,146,60,.36)}
.project-color-global{color:var(--muted);background:rgba(127,127,127,.11);border-color:var(--line)}

/* Keep the v9.5.3 account palette visible in the v9.6.2 Accounts renderer. Tracking
   cards already use the same deterministic palette through webgui_v953. */
#panel-accounts tbody tr td:first-child{box-shadow:inset 4px 0 var(--v962-account-accent,var(--accent));padding-left:12px}
#panel-accounts tbody tr:nth-child(12n+1){--v962-account-accent:#64b5f6}
#panel-accounts tbody tr:nth-child(12n+2){--v962-account-accent:#81c784}
#panel-accounts tbody tr:nth-child(12n+3){--v962-account-accent:#ffb74d}
#panel-accounts tbody tr:nth-child(12n+4){--v962-account-accent:#ba68c8}
#panel-accounts tbody tr:nth-child(12n+5){--v962-account-accent:#4dd0e1}
#panel-accounts tbody tr:nth-child(12n+6){--v962-account-accent:#f06292}
#panel-accounts tbody tr:nth-child(12n+7){--v962-account-accent:#aed581}
#panel-accounts tbody tr:nth-child(12n+8){--v962-account-accent:#ffd54f}
#panel-accounts tbody tr:nth-child(12n+9){--v962-account-accent:#7986cb}
#panel-accounts tbody tr:nth-child(12n+10){--v962-account-accent:#4db6ac}
#panel-accounts tbody tr:nth-child(12n+11){--v962-account-accent:#ff8a65}
#panel-accounts tbody tr:nth-child(12n+12){--v962-account-accent:#90a4ae}

.badge{border-color:color-mix(in srgb,var(--v963-accent,var(--accent)) 38%,var(--line));background:color-mix(in srgb,var(--v963-accent,var(--accent)) 9%,var(--card))}
.notice{border-color:color-mix(in srgb,var(--v963-accent,var(--accent)) 24%,var(--line))}

@media(max-width:820px){
  .shell{grid-template-columns:1fr}
  .v962-nav{position:static;height:auto;flex-direction:row;flex-wrap:nowrap;gap:4px;overflow:auto;border-right:0;border-bottom:1px solid var(--line);padding:8px;box-shadow:none;background:#0d1014}
  .v962-brand,.v962-nav-label{display:none}
  .v962-nav a.v962-nav-link{width:auto;white-space:nowrap;padding:7px 9px}
  .v962-ico{width:auto;min-width:0}
  .v962-legacy-links{display:flex;gap:4px;margin:0;padding:0;border:0;white-space:nowrap}
  .v962-legacy-links a{padding:7px 9px}
}
'''


def _link(view: str, label: str, icon: str) -> str:
    return (
        f'<a class="v962-nav-link" href="#{escape(view)}" '
        f'data-v962-nav="{escape(view)}">'
        f'<span class="v962-ico" aria-hidden="true">{escape(icon)}</span>{escape(label)}</a>'
    )


def restored_nav() -> str:
    parts = [
        '<nav class="v962-nav" aria-label="Dashboard sections">',
        '<div class="v962-brand"><strong>Postmaster</strong>'
        '<small>WebGUI v9.6.2 · lazy fragments</small></div>',
    ]
    for heading, links in NAV_GROUPS:
        parts.append(f'<div class="v962-nav-label">{escape(heading)}</div>')
        parts.extend(_link(view, label, icon) for view, label, icon in links)
    parts.append(
        '<div class="v962-legacy-links">'
        '<a href="#domains" data-v962-nav="domains">Domain controls</a>'
        '<a href="#recipients" data-v962-nav="recipients">Recipient controls</a>'
        '</div>'
    )
    parts.append('</nav>')
    return ''.join(parts)


def install_webgui_visual_restoration(v962: Any) -> None:
    """Restore pre-v9.6.2 presentation while leaving the v9.6.2 lazy engine intact."""
    if "webgui-pre-v962-color-restoration" not in str(v962.BASE_STYLE):
        v962.BASE_STYLE += RESTORED_STYLE
    v962._nav = restored_nav


__all__ = ["NAV_GROUPS", "RESTORED_STYLE", "install_webgui_visual_restoration", "restored_nav"]
