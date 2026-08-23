from __future__ import annotations

from typing import Any

from . import runtime_control


_READ_ONLY_RUNTIME_OPERATIONS = {"status", "check-update", "list-versions"}
_MUTATING_RUNTIME_OPERATIONS = {"update-latest", "switch-version", "pin-version", "rollback-version"}


def _runtime_selector(status: dict[str, Any]) -> str:
    existing = runtime_control.read_control()
    raw = existing.get("selector") or status.get("requested_version") or status.get("version") or "latest"
    try:
        return runtime_control.canonical_selector(str(raw))
    except ValueError:
        return str(raw)


def _version_operation_target(operation: str, target_version: str | None) -> str:
    if operation == "update-latest":
        if target_version not in (None, "", "latest"):
            raise ValueError("update-latest target is fixed to latest")
        return "latest"
    if operation not in {"switch-version", "pin-version", "rollback-version"}:
        raise ValueError(f"unsupported mutating runtime operation: {operation}")
    target = runtime_control.canonical_selector(target_version)
    if target == "latest":
        raise ValueError(f"{operation} requires an explicit stable vX.Y.Z target")
    return target


def _version_preview(
    status: dict[str, Any],
    *,
    operation: str,
    target: str,
    selector: str,
) -> dict[str, Any]:
    build = str(status.get("build") or "unknown")
    version = str(status.get("version") or "unknown")
    token = runtime_control.issue_version_change_approval(
        operation=operation,
        target=target,
        current_selector=selector,
        current_build=build,
    )
    return {
        **status,
        "runtime_operation": operation,
        "runtime_operation_kind": (
            "update_to_latest" if operation == "update-latest"
            else "rollback" if operation == "rollback-version"
            else "pin_version" if operation == "pin-version"
            else "version_switch"
        ),
        "current_version": version,
        "current_build": build,
        "current_selector": selector,
        "target_version_ref": target,
        "approval_required": True,
        "version_change_applied": False,
        "confirmation_token": token,
        "confirmation_scope": "single operation + exact target + current runtime state + active chat",
        "next_step": (
            "Show the current version/build, selector, exact target and operation to the user. "
            "Ask for explicit approval in the active chat. Only after that explicit reply may "
            "this one-time confirmation_token be sent back as confirm_version_change."
        ),
    }


def install_runtime_v964(base: Any, core: Any, legacy_build_status: Any):
    """v9.6.4 mail-policy and runtime-admin overlay. Existing tool names only."""

    def build_status(
        operation: str = "status",
        target_version: str | None = None,
        force_refresh: bool = False,
        confirm_version_change: str | None = None,
    ):
        """Read runtime status or safely request an application version change.

        `status`, `check-update`, and `list-versions` are read-only and require no approval.
        `force_refresh` is read-only and only refreshes stable-release discovery; it never changes
        the selector and never authorizes a version change.

        `update-latest`, `switch-version`, `pin-version`, and `rollback-version` are WRITE ACTIONS.
        A first call without `confirm_version_change` is preview-only: it returns current
        version/build, current selector, exact target, operation type, and a one-time token but
        makes no change. The assistant MUST then show those details and obtain an explicit user
        approval in the active chat. Only after that reply may the exact returned token be passed
        as `confirm_version_change` for that same operation and target. Never infer approval from
        prior or generic instructions, never reuse a token, and never carry approval across chats.
        Changing the target or operation requires a new preview and new user approval.
        """
        status = legacy_build_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status.update({
            "version_capability": "9.6.4",
            "outbound_historical_tracking_normalized": True,
            "manual_webgui_recipient_policy": True,
            "suppression_confirmation_per_send": True,
            "mcp_runtime_version_control": True,
            "mcp_version_change_requires_explicit_chat_approval": True,
            "new_mail_mcp_commands": 0,
            "mcp_command_count_expected": 90,
        })

        op = str(operation or "status").strip().lower()
        selector = _runtime_selector(status)
        if op in _READ_ONLY_RUNTIME_OPERATIONS:
            if confirm_version_change:
                return {
                    **status,
                    "ok": False,
                    "error": "confirm_version_change is not accepted for read-only operations",
                    "version_change_applied": False,
                }
            if op == "status" and force_refresh:
                return {
                    **status,
                    "ok": False,
                    "error": "force_refresh must use check-update or list-versions",
                    "version_change_applied": False,
                }
            if op in {"check-update", "list-versions"}:
                versions, release_status = runtime_control.stable_release_tags(force=force_refresh)
                latest = versions[0] if versions else None
                current = runtime_control.semver_tuple(str(status.get("version") or ""))
                latest_tuple = runtime_control.semver_tuple(latest)
                update_available = latest_tuple > current if latest_tuple and current else None
                status.update({
                    "runtime_operation": op,
                    "current_selector": selector,
                    "available_versions": versions,
                    "latest_version_ref": latest,
                    "update_available": update_available,
                    "stable_release_check_status": release_status,
                    "force_refresh": bool(force_refresh),
                    "version_change_applied": False,
                    "approval_required": False,
                })
            return status

        if op not in _MUTATING_RUNTIME_OPERATIONS:
            return {
                **status,
                "ok": False,
                "error": f"unsupported runtime operation: {op}",
                "version_change_applied": False,
            }
        if force_refresh:
            return {
                **status,
                "ok": False,
                "error": "force_refresh is read-only and cannot be combined with version changes",
                "version_change_applied": False,
            }

        try:
            target = _version_operation_target(op, target_version)
        except ValueError as exc:
            return {**status, "ok": False, "error": str(exc), "version_change_applied": False}

        if target != "latest":
            versions, release_status = runtime_control.stable_release_tags(force=False)
            if release_status != "ok" or target not in versions:
                return {
                    **status,
                    "ok": False,
                    "error": f"target {target} is not a verified stable release",
                    "available_versions": versions,
                    "stable_release_check_status": release_status,
                    "version_change_applied": False,
                }

        build = str(status.get("build") or "unknown")
        if not confirm_version_change:
            return _version_preview(status, operation=op, target=target, selector=selector)

        approved = runtime_control.consume_version_change_approval(
            confirm_version_change,
            operation=op,
            target=target,
            current_selector=selector,
            current_build=build,
        )
        if not approved:
            return {
                **status,
                "ok": False,
                "runtime_operation": op,
                "current_version": str(status.get("version") or "unknown"),
                "current_build": build,
                "current_selector": selector,
                "target_version_ref": target,
                "approval_required": True,
                "version_change_applied": False,
                "error": (
                    "missing, expired, already-used, or mismatched version-change approval; "
                    "request a new preview and obtain new explicit user approval"
                ),
            }

        if op == "update-latest":
            control = runtime_control.write_control(selector="latest", check_updates_once=True)
        else:
            control = runtime_control.write_control(selector=target)
        runtime_control.schedule_current_process_termination()
        return {
            **status,
            "runtime_operation": op,
            "current_version": str(status.get("version") or "unknown"),
            "current_build": build,
            "previous_selector": selector,
            "target_version_ref": target,
            "requested_selector": control.get("selector"),
            "update_check_requested": bool(control.get("check_updates_once")),
            "approval_required": False,
            "version_change_applied": True,
            "restart_scheduled": True,
            "production_runtime_changed": False,
            "note": "Control state accepted; the requested version becomes effective only after restart/bootstrap completes.",
        }

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    base.build_status = build_status
    core.build_status = build_status

    def send_email(
        to: list[str], subject: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        body_amp: str | None = None, attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None, campaign_id: str | None = None,
        account_id: str | None = None, newsletter_mode: bool = False,
        unsubscribe_url: str | None = None, unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False, automatic_unsubscribe: bool = True,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Send email through the existing outbound pipeline.

        Suppressed recipients are blocked by default. If a call reports that suppression
        authorization is required, ask the user to explicitly approve the exact suppressed
        address(es) for this specific send before retrying. Only after that approval may
        `confirm_suppressed_recipients` contain those exact addresses. The confirmation is
        ephemeral and does not alter recipient authorization or the suppression list.
        """
        return base._safe_call(
            base.mail_client(account_id).send_email,
            to=to, subject=subject, body=body, cc=cc, bcc=bcc, body_html=body_html,
            body_amp=body_amp, attachments=attachments, track_opens=track_opens,
            campaign_id=campaign_id, newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            automatic_unsubscribe=automatic_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("send_email")
    core.mcp.add_tool(send_email, name="send_email")
    base.send_email = send_email
    core.send_email = send_email

    def reply_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Reply in-thread through the existing outbound pipeline.

        Suppressed recipients require explicit user approval for this single reply before their
        exact addresses may be passed in `confirm_suppressed_recipients`. Never infer or persist
        that approval; retry only after the user has approved the addresses named by the block.
        """
        return base._safe_call(
            base.mail_client(account_id).reply_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc, body_html=body_html,
            attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("reply_email")
    core.mcp.add_tool(reply_email, name="reply_email")
    base.reply_email = reply_email
    core.reply_email = reply_email

    def follow_up_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None,
        bcc: list[str] | None = None, body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None,
        newsletter_mode: bool = False, unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None,
        force_send: bool = False, confirm_suppressed_recipients: list[str] | None = None,
    ):
        """WRITE ACTION. Follow up on an outbound message through the existing pipeline.

        Suppressed recipients require explicit user approval for this single follow-up before
        their exact addresses may be passed in `confirm_suppressed_recipients`. The approval is
        per-send only and must not be inferred from prior sends or stored as future authorization.
        """
        return base._safe_call(
            base.mail_client(account_id).follow_up_email,
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc, body_html=body_html,
            attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key,
            force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    core.mcp.remove_tool("follow_up_email")
    core.mcp.add_tool(follow_up_email, name="follow_up_email")
    base.follow_up_email = follow_up_email
    core.follow_up_email = follow_up_email
    return build_status


__all__ = ["install_runtime_v964"]
