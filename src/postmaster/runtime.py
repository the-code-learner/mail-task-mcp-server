from __future__ import annotations

import os

import uvicorn

from . import runtime_core as _core
from .project_scope_semantics import install_project_scope_semantics
from .runtime_v946 import install_runtime_v946
from .runtime_v950 import install_runtime_v950
from .runtime_v953 import install_runtime_v953
from .runtime_v960 import install_runtime_v960
from .runtime_v960_knowledge import install_runtime_v960_knowledge
from .runtime_v961 import install_runtime_v961
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
dashboard_home = install_webgui_v962(app, _base, _core, dashboard_home)
link_store = _core.link_store


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
        log_level="info",
    )
