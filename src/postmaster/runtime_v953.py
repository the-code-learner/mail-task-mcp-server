from __future__ import annotations

import os
from typing import Any


def install_runtime_v953(base: Any, core: Any, legacy_build_status: Any):
    """Add truthful runtime-control observability without adding MCP command names."""

    def build_status():
        status = legacy_build_status()
        if isinstance(status, dict):
            resolved = str(status.get("build") or os.getenv("POSTMASTER_REF") or "unknown")
            requested = (
                os.getenv("POSTMASTER_REQUESTED_VERSION")
                or os.getenv("POSTMASTER_VERSION")
                or resolved
            )
            status["requested_version"] = requested
            status["runtime_admin_control"] = True
            status["new_mail_mcp_commands"] = 0
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    core.build_status = build_status
    base.build_status = build_status
    return build_status
