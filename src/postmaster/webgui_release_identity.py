from __future__ import annotations

from typing import Any


def install_webgui_release_identity(v962: Any, version: str) -> None:
    """Overlay the reused v9.6.2 shell with the actual runtime release identity."""
    release = str(version or "unknown").strip().removeprefix("v") or "unknown"
    original_nav = v962._nav
    original_shell = v962._shell

    def release_nav() -> str:
        return original_nav().replace(
            "WebGUI v9.6.2 · lazy fragments",
            f"WebGUI v{release} · lazy fragments",
            1,
        )

    def release_shell(request: Any):
        response = original_shell(request)
        response.headers["X-Postmaster-WebGUI"] = f"{release}-lazy"
        return response

    v962._nav = release_nav
    v962._shell = release_shell


__all__ = ["install_webgui_release_identity"]
