from __future__ import annotations

import os

import uvicorn

from . import runtime_core as _core
from . import webgui_v962 as _webgui_v962
from . import webgui_v963 as _webgui_v963
from .project_scope_semantics import install_project_scope_semantics
from .runtime_v946 import install_runtime_v946
from .runtime_v950 import install_runtime_v950
from .runtime_v953 import install_runtime_v953
from .runtime_v960 import install_runtime_v960
from .runtime_v960_knowledge import install_runtime_v960_knowledge
from .runtime_v961 import install_runtime_v961
from .runtime_v963 import install_runtime_v963
from .runtime_v964 import install_runtime_v964
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
# v9.6.3 is additive: runtime/cache first, then privacy hooks and the presentation/Inbox
# renderer layer over the already-installed v9.6.2 lazy shell. It does not replace that shell's
# JS lifecycle and it does not add MCP command names.
build_status = install_runtime_v963(_base, _core, build_status)
install_webgui_v963_high_noise(_webgui_v963)
install_webgui_v963(app, _base)
# v9.6.4 keeps the public mail tool names/count stable while changing only outbound policy
# semantics: canonical detracking, manual WebGUI recipient policy and per-send suppression consent.
build_status = install_runtime_v964(_base, _core, build_status)
install_webgui_v964(app, _base)
# The lazy shell originates in v9.6.2 but must identify the release that is actually loaded.
# VERSION is local release metadata, so this does not add a network lookup to WebGUI rendering.
install_webgui_release_identity(_webgui_v962, project_release_version())
# Keep the public v9.6.0/v9.6.3 renderer symbols on the explicit compatibility wrapper rather
# than the transient lambda used during route installation. The wrapper remains cache-first.
_webgui_v963.v960.render_inbox = _webgui_v963.render_inbox_v963
_webgui_v963.v951.render_inbox = _webgui_v963.render_inbox_v963
link_store = _core.link_store


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
