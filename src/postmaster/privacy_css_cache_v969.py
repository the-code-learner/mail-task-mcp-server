from __future__ import annotations

from typing import Any

from .email_inventory_v963 import inventory_message
from .privacy_cache_v969 import PassiveContentService
from .privacy_css_v969 import BoundedPassiveContentService


class CacheAwareBoundedPassiveContentService(BoundedPassiveContentService):
    """Make cache-only MCP/WebGUI reads report nested CSS state without network access."""

    def render_cached_message(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, Any]:
        cache = self.base.mailbox_cache_store()
        detail = cache.get_message(account_id, mailbox, str(uid), include_body=True)
        if not detail or not detail.get("body_cached"):
            return PassiveContentService.render_cached_message(
                self,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )

        body_html = str(detail.get("body_html") or "")
        inventory = inventory_message(body_html, str(detail.get("body") or ""))
        nested = self._process_stylesheets(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=False,
            network_allowed=False,
        )
        result = PassiveContentService.render_cached_message(
            self,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        return self._merge_nested_result(
            result,
            nested,
            body_html=body_html,
            inventory=inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )


__all__ = ["CacheAwareBoundedPassiveContentService"]
