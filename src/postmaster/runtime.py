from __future__ import annotations

import os

import uvicorn

from . import runtime_core as _core
from . import webgui_projects as _webgui_projects
from . import webgui_v960 as _webgui_v960
from . import webgui_v962 as _webgui_v962
from . import webgui_v962_views as _webgui_v962_views
from . import webgui_v963 as _webgui_v963
from . import webgui_v964 as _webgui_v964
from .project_scope_semantics import install_project_scope_semantics
from .runtime_v946 import install_runtime_v946
from .runtime_v950 import install_runtime_v950
from .runtime_v953 import install_runtime_v953
from .runtime_v960 import install_runtime_v960
from .runtime_v960_knowledge import install_runtime_v960_knowledge
from .runtime_v961 import install_runtime_v961
from .runtime_v963 import install_runtime_v963
from .runtime_v964 import install_runtime_v964
from .runtime_v966 import install_runtime_v966
from .runtime_v967 import install_runtime_v967
from .runtime_v968 import install_runtime_v968
from .runtime_v969 import install_runtime_v969_mcp, install_runtime_v969_pre_webgui
from .tracking_telemetry_v971 import (
    install_tracking_telemetry_v971,
    install_tracking_webgui_v971,
)
from .webgui_compose_confirmation_v971 import install_compose_confirmation_v971
from .webgui_mail_files_v971 import install_mail_files_composer_v971
from .webgui_projects_ux_v971 import install_projects_ux_v971
from .webgui_regressions_v971 import (
    install_full_html_partial_success_v971,
    install_webgui_interaction_regressions_v971,
)
from .webgui_release_identity import install_webgui_release_identity, project_release_version
from .webgui_tasks import task_fragment as _task_fragment_v945
from .webgui_v945 import install_webgui_v945
from .webgui_v951 import install_webgui_v951
from .webgui_v952 import install_webgui_v952
from .webgui_v953 import install_webgui_v953
from .webgui_v954 import install_webgui_v954
from .webgui_v960 import install_webgui_v960
from .webgui_v960_scopes import install_webgui_v960_scopes
from .webgui_v961 import install_webgui_v961
from .webgui_v962 import install_webgui_v962
from .webgui_v962_collapsible import install_webgui_v962_collapsible_system
from .webgui_v963 import install_webgui_v963
from .webgui_v963_high_noise import install_webgui_v963_high_noise
from .webgui_v964 import install_webgui_v964
from .webgui_v970 import install_webgui_v970
from .webgui_visual_restoration import install_webgui_visual_restoration

for _name in dir(_core):
    if _name.startswith("_"):
        continue
    globals()[_name] = getattr(_core, _name)

_base = _core._base
mcp = _core.mcp
app = _core.app
_tracking_dashboard_fragment = _core._tracking_dashboard_fragment


def _task_dashboard_fragment(request):
    # Compatibility helper retained for v9.4.4 WebGUI regression coverage.
    return _task_fragment_v945(_base, request)


dashboard_home = install_webgui_v945(app, _base, _core.dashboard_home)
dashboard_home, build_status, mail_client = install_runtime_v946(
    app, _base, _core, dashboard_home
)
dashboard_home, build_status, mail_client = install_runtime_v950(
    app, _base, _core, dashboard_home, build_status
)
build_status = install_runtime_v953(_base, _core, build_status)
dashboard_home, build_status, mail_client = install_runtime_v960(
    app, _base, _core, dashboard_home, build_status
)
# Install v9.6.2 Unassigned semantics before the scope-store singleton can bootstrap.
install_project_scope_semantics()
install_runtime_v960_knowledge(_base, _core)
install_runtime_v961()
dashboard_home = install_webgui_v951(app, _base, _core, dashboard_home)
dashboard_home = install_webgui_v952(app, _base, dashboard_home)
dashboard_home = install_webgui_v953(app, _base, _core, dashboard_home)
dashboard_home = install_webgui_v954(app, _base, _core, dashboard_home)
dashboard_home = install_webgui_v960(app, _base, _core, dashboard_home)
install_webgui_v960_scopes(app, _base)
install_webgui_v961()
install_webgui_v962_collapsible_system()
dashboard_home = install_webgui_v962(app, _base, _core, dashboard_home)
# Post-v9.7.0 sensitive tracking telemetry uses the existing analytics DB through a private
# account-scoped sidecar. Existing MCP tracking responses remain free of raw source IP data.
install_tracking_telemetry_v971(_base, _core)
# v9.6.3 is additive: runtime/cache first, then privacy hooks and the presentation/Inbox
# renderer layer over the already-installed v9.6.2 lazy shell. It does not replace that shell's
# JS lifecycle and it does not add MCP command names.
build_status = install_runtime_v963(_base, _core, build_status)
install_webgui_v963_high_noise(_webgui_v963)
# v9.6.8 hardens the final composed boundaries before v9.6.3 routes capture their handlers:
# individualized sends must canonicalize historical tracking first; Full HTML propagates safe
# success/partial/failure state; successful received-message detail opens update IMAP/cache Seen.
# This layer adds no MCP command names and does not change deployment YAML or production state.
install_runtime_v968(_base, _core, _webgui_v963)
# v9.6.9 replaces the split WebGUI privacy path with one persistent shared service, installs
# opaque hash resource keys and fixes the logical-send/Sent archive boundary before routes capture
# the handlers. Source-only: deployment YAML and production state remain untouched.
_passive_content_service = install_runtime_v969_pre_webgui(_base, _core, _webgui_v963)
# Post-v9.7.0: preserve the same consent/cache/proxy boundary, but distinguish isolated passive
# resource failures from an unusable Full HTML document. The shared service feeds WebGUI + MCP.
install_full_html_partial_success_v971(_passive_content_service)
install_webgui_v963(app, _base)
# v9.6.4 keeps the public mail tool names/count stable while changing only outbound policy
# semantics: canonical detracking, manual WebGUI recipient policy and per-send suppression consent.
build_status = install_runtime_v964(_base, _core, build_status)
install_webgui_v964(app, _base)
# v9.6.6 extends only the existing Privacy Proxy admin tool and status surfaces. The generated
# HMAC secret and Ed25519 private key remain server-side.
build_status = install_runtime_v966(_base, _core, build_status)
# v9.6.7 freezes the v9.6.6 legacy command schemas and adds six lifecycle-stable commands split
# by status / preview / execute. No legacy command name is removed or re-registered in this layer.
runtime_status = install_runtime_v967(_base, _core, build_status)
# v9.6.9 adds one explicit networked passive-resource command (safe get_email stays zero-network)
# and replaces client-carried bearer confirmation tokens with persistent server-side pending
# previews. The two execute schemas no longer require a secret-like confirmation token.
runtime_status = install_runtime_v969_mcp(_base, _core, runtime_status)
# Restore the richer pre-v9.6.2 navigation/palette only after all functional WebGUI overlays are
# installed. The installer changes presentation on the existing lazy shell and does not replace
# fragment routes, renderers or outbound handlers.
install_webgui_visual_restoration(_webgui_v962)
# v9.7.0 is presentation-only. It overlays the already-frozen v9.6.9 shell with enterprise
# hierarchy, responsive navigation, theme/accessibility tokens and mobile drill-down behavior.
# It receives no app/backend registry and therefore cannot add or replace routes.
install_webgui_v970(_webgui_v962)
# Keep the post-v9.7.0 interaction corrections last in the effective cascade: horizontal table
# containment remains, while vertical wheel chaining and intrinsic card heights are restored.
install_webgui_interaction_regressions_v971(_webgui_v962)
# Tracking is contextualized in Inbox/Sent after the final v9.7.0 cascade. It is read-only UI:
# sensitive network telemetry stays server-side and a rendering failure never blocks mail access.
install_tracking_webgui_v971(_base, _core, _webgui_v963, _webgui_v962)
# Add cache-only attachment viewing and the Inbox-first floating composer only after contextual
# Tracking is complete. Legacy direct Compose/Tracking views remain routable but leave top-level IA.
install_mail_files_composer_v971(app, _base, _webgui_v963, _webgui_v962, _webgui_v964)
# Preserve every selected Stored File across the explicit suppression-warning confirmation POST.
install_compose_confirmation_v971(_webgui_v964)
# Project-scoped UX is presentation-only: reuse existing Memories/Skills/Files/Tasks stores and
# CRUD/auth routes, while keeping editors contextual and project selection explicit.
install_projects_ux_v971(_webgui_v960, _webgui_projects, _webgui_v962_views, _webgui_v962)
# The lazy shell originates in v9.6.2 but must identify the release that is actually loaded.
# VERSION is local release metadata, so this does not add a network lookup to WebGUI rendering.
install_webgui_release_identity(_webgui_v962, project_release_version())

# Preserve the v9.6.3 production composition boundary after every functional overlay is installed.
# At this point render_inbox_v963 already references the final post-v9.7.0 Inbox renderer.
_webgui_v963.v960.render_inbox = _webgui_v963.render_inbox_v963


def _render_inbox_compat(proxied_base, request):
    """Stable compatibility entry point for the composed Safe Reader.

    The delegated v9.6.3+ renderer continues to request inspection="full" with
    content_mode="safe" by default; later overlays may enrich the returned HTML but
    cannot weaken that zero-network Safe Email boundary.
    """
    return _webgui_v963.render_inbox_v963(proxied_base, request)


# Keep the public v9.6.0/v9.6.3 renderer symbols on an explicit compatibility wrapper rather
# than a transient lambda or a feature-specific renderer. The wrapper remains cache-first and
# preserves the historical Safe Reader source contract while delegating to the final composition.
_webgui_v963.v960.render_inbox = _render_inbox_compat
_webgui_v963.v951.render_inbox = _render_inbox_compat
link_store = _core.link_store


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
