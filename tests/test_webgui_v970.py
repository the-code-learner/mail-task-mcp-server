from __future__ import annotations

import inspect
from types import SimpleNamespace

from starlette.responses import HTMLResponse

from postmaster.webgui_release_identity import install_webgui_release_identity
from postmaster.webgui_v962_views import VIEWS
from postmaster.webgui_v970 import (
    ENTERPRISE_SCRIPT,
    ENTERPRISE_STYLE,
    NAV_GROUPS,
    VIEW_LABELS,
    enterprise_nav,
    install_webgui_v970,
)


def _fake_shell(_request):
    return HTMLResponse(
        '<!doctype html><html><head></head><body><div class="shell">'
        '<nav>legacy</nav><main>'
        '<form method="post" action="/dashboard/compose/send">'
        '<input type="hidden" name="csrf" value="fixed-token">'
        '<input name="subject" value="Existing payload">'
        '<button type="submit" name="compose_action" value="send">Send</button>'
        '</form></main></div><script id="existing-lifecycle">window.existing=true;</script>'
        '</body></html>',
        headers={"Cache-Control": "private, no-store", "X-Postmaster-WebGUI": "9.6.2-lazy"},
    )


def _fake_v962():
    return SimpleNamespace(
        BASE_STYLE="/* baseline */",
        SCRIPT='<script id="baseline-script">window.baseline=true;</script>',
        _nav=lambda: '<nav>legacy</nav>',
        _shell=_fake_shell,
    )


def test_v970_installer_has_no_app_or_backend_registry_parameter():
    assert list(inspect.signature(install_webgui_v970).parameters) == ["v962"]


def test_v970_navigation_preserves_every_existing_lazy_view():
    covered = {
        view
        for _heading, links in NAV_GROUPS
        for view, _label, _code in links
    }
    assert covered == set(VIEWS)
    assert set(VIEW_LABELS) == set(VIEWS)
    nav = enterprise_nav()
    for view in VIEWS:
        assert f'data-v962-nav="{view}"' in nav
        assert f'data-v970-nav="{view}"' in nav


def test_v970_shell_overlay_preserves_existing_form_contract_and_script():
    v962 = _fake_v962()
    install_webgui_v970(v962)

    response = v962._shell(object())
    html = response.body.decode("utf-8")

    assert 'method="post" action="/dashboard/compose/send"' in html
    assert 'name="csrf" value="fixed-token"' in html
    assert 'name="subject" value="Existing payload"' in html
    assert 'name="compose_action" value="send"' in html
    assert 'id="existing-lifecycle"' in html
    assert 'class="v970-workspace"' in html
    assert 'class="v970-contextbar"' in html
    assert response.headers["x-postmaster-webgui-design"] == "v9.7.0-enterprise-refresh"
    assert response.headers["cache-control"] == "private, no-store"


def test_v970_install_is_idempotent_for_style_and_script():
    v962 = _fake_v962()
    install_webgui_v970(v962)
    install_webgui_v970(v962)
    assert v962.BASE_STYLE.count("webgui-v970-enterprise-operational-refresh") == 1
    assert v962.SCRIPT.count('id="v970-enterprise-shell"') == 1


def test_v970_release_identity_still_uses_local_release_version():
    v962 = _fake_v962()
    install_webgui_v970(v962)
    install_webgui_release_identity(v962, "9.7.0")
    assert "WebGUI v9.7.0 · lazy fragments" in v962._nav()
    response = v962._shell(object())
    assert response.headers["x-postmaster-webgui"] == "9.7.0-lazy"


def test_v970_responsive_accessibility_and_theme_contracts_are_present():
    assert "@media(max-width:1279px)" in ENTERPRISE_STYLE
    assert "@media(max-width:767px)" in ENTERPRISE_STYLE
    assert "@media(max-width:430px)" in ENTERPRISE_STYLE
    assert "prefers-reduced-motion:reduce" in ENTERPRISE_STYLE
    assert "prefers-color-scheme:light" in ENTERPRISE_STYLE
    assert 'data-v970-theme="light"' in ENTERPRISE_STYLE
    assert ":focus-visible" in ENTERPRISE_STYLE
    assert "v970-mobile-nav" in ENTERPRISE_STYLE
    assert "task-calendar-grid" in ENTERPRISE_STYLE
    assert "#panel-inbox:has(.v963-detail)" in ENTERPRISE_STYLE
    assert "MutationObserver" in ENTERPRISE_SCRIPT
    assert "aria-expanded" in ENTERPRISE_SCRIPT


def test_v970_mobile_navigation_is_non_semantic_progressive_enhancement():
    nav = enterprise_nav()
    assert 'aria-label="Mobile primary navigation"' in nav
    assert 'data-v970-more' in nav
    assert 'aria-controls="v970-more-sheet"' in nav
    # Existing lazy navigation attributes remain the only section-routing contract.
    assert 'data-v962-nav="overview"' in nav
    assert 'data-v962-nav="inbox"' in nav
    assert 'data-v962-nav="compose"' in nav
    assert 'data-v962-nav="scheduler"' in nav
