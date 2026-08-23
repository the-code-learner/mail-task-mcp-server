from __future__ import annotations

import unittest
from unittest.mock import patch

from postmaster import webgui_v951 as v951
from postmaster.webgui_v962_collapsible import install_webgui_v962_collapsible_system


class CollapsibleSystemV962Tests(unittest.TestCase):
    def test_secondary_runtime_actions_are_collapsible_and_status_stays_visible(self):
        sample = '''
<section class="tab-panel" id="panel-system" data-panel="system">
<div class="v951-metrics">always visible status</div>
<div class="v953-system-actions">
<section class="card"><h3>Restart current version</h3><form>restart</form></section>
<section class="card"><h3>Update to latest stable</h3><form>latest</form></section>
<section class="card v953-warning"><h3>Select stable version</h3><form>switch</form></section>
</div>
</section>'''
        original = v951.render_system
        try:
            with patch.object(v951, "render_system", return_value=sample):
                # Reset the module-local guard by reloading only the installer module.
                import postmaster.webgui_v962_collapsible as collapsible
                collapsible._INSTALLED = False
                collapsible.install_webgui_v962_collapsible_system()
                html = v951.render_system(object(), object())
            self.assertIn("always visible status", html)
            self.assertEqual(html.count('class="v962-collapsible"'), 3)
            self.assertIn("<summary>Restart current version</summary>", html)
            self.assertIn("<summary>Update to latest stable</summary>", html)
            self.assertIn("<summary>Select stable version</summary>", html)
            self.assertNotIn('<details class="v962-collapsible" data-v962-state-key="system-status"', html)
        finally:
            v951.render_system = original
            import postmaster.webgui_v962_collapsible as collapsible
            collapsible._INSTALLED = False


if __name__ == "__main__":
    unittest.main()
