from __future__ import annotations

import json
import time
import unittest

from postmaster.webgui_design_v985 import (
    DESIGN_SCRIPT, DESIGN_STYLE, contextbar, navigation,
)
from postmaster.webgui_overview_v985 import OVERVIEW_STYLE
from postmaster.webgui_settings_v985 import SETTINGS_STYLE
from postmaster.webgui_v970 import ENTERPRISE_STYLE,ENTERPRISE_SCRIPT,VIEW_LABELS
from test_webgui_v970 import _serve_browser_document,_browser_session


def _document():
    header=contextbar("9.8.5").replace(
        '<div class="v970-context-actions">',
        '<div class="v984-scopebar" id="v984-scopebar">'
        '<label>Owner<select data-v984-scope="owner_id"><option>All owners</option></select></label>'
        '<label>Project<select data-v984-scope="project_id"><option>All projects</option></select></label>'
        '<label>Account<select data-v984-scope="account_id"><option>All accounts</option></select></label>'
        '</div><div class="v970-context-actions">',1,
    )
    main=(
        '<section class="tab-panel active" data-panel="overview" id="panel-overview">'
        '<div class="v985-overview"><div class="v985-pagehead"><h2>Overview</h2></div>'
        '<div class="v985-version-grid">'
        '<div class="v985-version-card"><span>Installed version</span><strong>v9.8.5</strong></div>'
        '<div class="v985-version-card"><span>Latest available stable</span><strong>v9.8.5</strong></div>'
        '</div><div class="v985-overview-metrics">'
        +''.join('<div class="v985-overview-metric"><span>Metric</span><strong>7</strong></div>' for _ in range(4))
        +'</div><section class="v985-quick-access"><header>Quick access</header></section>'
        '</div></section>'
    )
    script=ENTERPRISE_SCRIPT.replace(
        "%VIEW_LABELS%",
        json.dumps({key:[v[0],v[1]] for key,v in VIEW_LABELS.items()}),
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>'+ENTERPRISE_STYLE+DESIGN_STYLE+OVERVIEW_STYLE+SETTINGS_STYLE+'</style>'
        '</head><body><div class="shell">'+navigation()
        +'<div class="v970-workspace">'+header+'<main>'+main+'</main></div>'
        '</div>'+script+DESIGN_SCRIPT+'</body></html>'
    )


class V985BrowserLayoutTests(unittest.TestCase):
    def test_responsive_real_design_shell(self):
        browser=_browser_session()
        with _serve_browser_document(_document()) as url:
            for width,height in [(1440,900),(800,800),(390,840)]:
                with self.subTest(viewport=(width,height)):
                    browser._call("Emulation.setDeviceMetricsOverride",{
                        "width":width,"height":height,"deviceScaleFactor":1,"mobile":False,
                    })
                    result=browser._call("Page.navigate",{"url":url})
                    self.assertFalse(result.get("result",{}).get("errorText"))
                    expression="""(() => {
                        if(document.readyState!=='complete') return null;
                        const rect = s=>document.querySelector(s)?.getBoundingClientRect();
                        const a=rect('.v985-header'), side=rect('.v985-sidebar'),
                              main=rect('.v970-workspace > main'), mobile=rect('.v970-mobile-nav');
                        if(!a||!main||!side||!mobile)return null;
                        return {
                          width:innerWidth, headerTop:a.top, headerWidth:a.width, mainWidth:main.width,
                          mainLeft:main.left, sidebarWidth:side.width,
                          sidebarVisible:getComputedStyle(document.querySelector('.v985-sidebar')).display!=='none',
                          mobileVisible:getComputedStyle(document.querySelector('.v970-mobile-nav')).display!=='none',
                          scopesInHeader:!!document.querySelector('.v985-header #v984-scopebar'),
                          overviewVisible:getComputedStyle(document.querySelector('#panel-overview')).display!=='none',
                          overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
                          bgcolor:getComputedStyle(document.body).backgroundColor,
                        };
                    })()"""
                    payload=None
                    for _ in range(80):
                        value=browser._call("Runtime.evaluate",{
                            "expression":expression,"returnByValue":True,
                        })
                        payload=value.get("result",{}).get("result",{}).get("value")
                        if isinstance(payload,dict):break
                        time.sleep(.03)
                    self.assertIsInstance(payload,dict)
                    self.assertTrue(payload["scopesInHeader"])
                    self.assertTrue(payload["overviewVisible"])
                    self.assertLessEqual(abs(payload["headerTop"]),1)
                    self.assertLessEqual(abs(payload["headerWidth"]-width),2)
                    self.assertLessEqual(payload["overflow"],2)
                    self.assertGreater(payload["mainWidth"],150)
                    if width>560:
                        self.assertTrue(payload["sidebarVisible"])
                        self.assertFalse(payload["mobileVisible"])
                        self.assertGreater(payload["mainLeft"],45)
                    else:
                        self.assertFalse(payload["sidebarVisible"])
                        self.assertTrue(payload["mobileVisible"])


if __name__=="__main__":
    unittest.main()
