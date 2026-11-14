"""Settings > GeoIP: explicitly authorized MaxMind database installation."""
from __future__ import annotations

import asyncio
import hmac
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from .geoip_download_v9899 import GeoIPDownloadError, install_geolite2
from .tracking_geoip_v984 import geoip_status


def geoip_settings_panel(base: Any, request: Request) -> str:
    status = geoip_status()
    city = "Installed" if status["city_database_available"] else "Not installed"
    asn = "Installed" if status["asn_database_available"] else "Not installed"
    isp = "Installed" if status["isp_database_available"] else "Not installed (optional, paid)"
    notice = str(request.query_params.get("geoip_notice") or "")
    flash = '<p class="v9899-geoip-notice">' + escape(notice) + '</p>' if notice else ""
    csrf = escape(str(base._csrf_value()), quote=True)
    return (
        '<section class="v985-settings-card v9899-geoip" id="v9899-geoip">'
        '<h3>GeoIP databases</h3>'
        '<p>Install or update local MaxMind GeoLite2 City and ASN databases '
        'to improve tracking location and network estimates. '
        'Existing events will be enriched when displayed. '
        'IP locations can identify proxies, not the email recipient.</p>'
        + flash +
        '<div class="v9899-geoip-status">'
        '<div><strong>City</strong><span>' + city + '</span></div>'
        '<div><strong>ASN</strong><span>' + asn + '</span></div>'
        '<div><strong>ISP</strong><span>' + isp + '</span></div></div>'
        '<p>GeoLite2 is free after registering a MaxMind account and accepting its terms. '
        'Enter your <a href="https://www.maxmind.com/en/geolite2/signup" '
        'target="_blank" rel="noopener noreferrer">MaxMind account</a> '
        'ID and a license key. The key is transmitted only when you press '
        'Download and is never saved by Postmaster.</p>'
        '<form method="post" action="/dashboard/geoip/install" '
        'autocomplete="off" data-v9899-geoip-install>'
        '<input type="hidden" name="csrf" value="' + csrf + '">'
        '<label>MaxMind Account ID<input type="text" name="account_id" '
        'inputmode="numeric" pattern="[0-9]+" maxlength="18" required '
        'autocomplete="off"></label>'
        '<label>MaxMind license key<input type="password" name="license_key" '
        'autocomplete="new-password" required maxlength="256"></label>'
        '<label class="v9899-geoip-consent"><input type="checkbox" '
        'name="license_confirm" value="yes" required> '
        'I have a MaxMind account and accept its GeoLite2 license terms.</label>'
        '<button type="submit" class="primary">Download / update GeoIP databases</button>'
        '<p class="small muted" data-v9899-progress hidden>Downloading and validating '
        'City and ASN databases… Do not submit twice.</p>'
        '</form>'
        '<p class="small muted">Downloads are requested only by clicking the button, '
        'not in the background. City and ASN are kept in the persistent /data volume. '
        'GeoIP2-ISP is a separate paid edition and is not downloaded by this form. '
        'For future updates reenter the license key and press the button again.</p>'
        '</section>'
    )


GEOIP_STYLE = r"""
.v9899-geoip{margin-top:16px}
.v9899-geoip>p{font-size:12px;line-height:1.6;color:var(--v970-muted)}
.v9899-geoip-status{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0}
.v9899-geoip-status>div{border:1px solid var(--v970-line);border-radius:9px;
padding:11px;display:flex;flex-direction:column;gap:6px;min-width:0}
.v9899-geoip-status span{font-size:12px;overflow-wrap:anywhere}
.v9899-geoip form{display:flex;flex-wrap:wrap;gap:12px;align-items:end}
.v9899-geoip form>label:not(.v9899-geoip-consent){display:flex;flex-direction:column;
gap:5px;flex:1;min-width:210px;font-size:12px}
.v9899-geoip input[type=text],.v9899-geoip input[type=password]{
max-width:100%;width:100%;padding:10px}
.v9899-geoip-consent{flex-basis:100%;display:flex;align-items:center;gap:8px;font-size:12px}
.v9899-geoip form>button{min-height:37px;padding:9px 13px}
.v9899-geoip-notice{padding:11px;border:1px solid var(--v970-line);
background:var(--v970-surface);border-radius:8px}
@media(max-width:720px){.v9899-geoip-status{grid-template-columns:1fr}}
"""

GEOIP_SCRIPT = r"""
<script id="v9899-geoip-script">
(()=>{"use strict";
 document.addEventListener('submit', event=>{
   const form=event.target.closest('form[data-v9899-geoip-install]');
   if(!form)return;
   const btn=form.querySelector('button[type=submit]');
   if(btn.disabled){event.preventDefault();return;}
   btn.disabled=true;btn.textContent='Downloading…';
   form.querySelector('[data-v9899-progress]').hidden=false;
 });
})();
</script>
"""


def install_geoip_settings(app: Any, base: Any, v962: Any) -> None:
    if any(isinstance(route, Route) and route.path == "/dashboard/geoip/install"
           for route in app.router.routes):
        return

    async def install(request: Request):
        form = await request.form()
        if not hmac.compare_digest(
            str(form.get("csrf") or ""), str(base._csrf_value())
        ):
            return RedirectResponse(
                "/?ui_view=system&settings_tab=geoip&geoip_notice=" +
                urlencode({"message":"Session expired, reload Settings before retrying"}).split("=",1)[1] +
                "#system",status_code=303,
                headers={"Cache-Control":"no-store"},
            )
        if str(form.get("license_confirm") or "") != "yes":
            msg = "Accept the MaxMind license terms before downloading"
        else:
            try:
                outcome = await asyncio.to_thread(
                    install_geolite2,
                    str(form.get("account_id") or ""),
                    str(form.get("license_key") or ""),
                )
                msg = ("GeoLite2 City and ASN installed successfully."
                       if outcome["ok"] else "Database installation incomplete.")
            except GeoIPDownloadError as exc:
                msg = str(exc)
            except Exception:
                msg = "GeoIP download failed. Check network connectivity and permissions."
        return RedirectResponse(
            "/?" + urlencode({
                "ui_view":"system","settings_tab":"geoip","geoip_notice":msg
            }) + "#system",
            status_code=303,
            headers={
                "Cache-Control":"private, no-store",
                "Referrer-Policy":"no-referrer",
                "X-Content-Type-Options":"nosniff",
            },
        )

    app.router.routes.insert(
        next((i for i,r in enumerate(app.router.routes)
              if isinstance(r,Route) and r.path=="/"),len(app.router.routes)),
        Route("/dashboard/geoip/install", install, methods=["POST"]),
    )
    prev=v962._styles
    def styles():
        return prev().rstrip()+"\n"+GEOIP_STYLE
    v962._styles=styles
    if 'id="v9899-geoip-script"' not in v962.SCRIPT:
        v962.SCRIPT += GEOIP_SCRIPT
