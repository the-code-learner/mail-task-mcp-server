from __future__ import annotations

from typing import Any

from .mcp_v969 import MCP_COMMAND_COUNT_V969, install_runtime_v969_mcp
from .outbound_archive_v969 import _install_outbound_archive_boundary
from .privacy_cache_v969 import PassiveContentService, install_hashed_resource_keys
from .webgui_v969 import _install_webgui_v969


def install_runtime_v969_pre_webgui(base: Any, core: Any, webgui_v963: Any) -> PassiveContentService:
    cache = base.mailbox_cache_store()
    install_hashed_resource_keys(cache)
    _install_outbound_archive_boundary()
    service = PassiveContentService(base)
    base.passive_content_service_v969 = lambda: service
    core.passive_content_service_v969 = lambda: service
    _install_webgui_v969(base, webgui_v963, service)
    return service


__all__ = [
    "MCP_COMMAND_COUNT_V969",
    "PassiveContentService",
    "install_hashed_resource_keys",
    "install_runtime_v969_mcp",
    "install_runtime_v969_pre_webgui",
]
