from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from postmaster import webgui_projects_ux_v971 as ux


class ProjectResourceUxV971Tests(unittest.TestCase):
    def test_memory_rows_open_from_title_without_view_button_and_keep_edit(self):
        html = (
            '<table><tbody><tr><td><strong>Canonical memory</strong><div class="small">id-1</div></td>'
            '<td><span>memory</span></td><td>scope</td><td>0.90</td><td class="actions">'
            '<a data-v960-fragment="knowledge" href="/?ui_view=knowledge&amp;view_knowledge=id-1#knowledge"><button type="button">View</button></a>'
            '<a data-v960-fragment="knowledge" href="/?ui_view=knowledge&amp;edit_knowledge=id-1#knowledge"><button type="button">Edit</button></a>'
            '</td></tr></tbody></table>'
        )
        rendered = ux._make_knowledge_rows_clickable(html)
        self.assertIn('data-v960-href="/?ui_view=knowledge&amp;view_knowledge=id-1#knowledge"', rendered)
        self.assertIn('<a class="v971-item-link"', rendered)
        self.assertIn("Canonical memory", rendered)
        self.assertNotIn(">View<", rendered)
        self.assertIn(">Edit<", rendered)

    def test_knowledge_editor_moves_after_inventory(self):
        html = (
            '<section class="tab-panel"><div class="grid">'
            '<section class="card wide"><h2>Add memory / skill</h2><form>editor</form></section>'
            '<section class="card wide"><h2>Search</h2></section>'
            '<section class="card wide"><table><tbody><tr><td>inventory</td></tr></tbody></table></section>'
            '</div></section>'
        )
        rendered = ux._move_knowledge_editor_after_inventory(html)
        self.assertGreater(rendered.index("Add memory / skill"), rendered.index("inventory"))

    def test_file_title_and_row_open_direct_download_without_view_button(self):
        html = (
            '<table><tbody><tr><td><strong>report.pdf</strong><div>file-1</div></td><td>owner</td><td>application/pdf</td><td>tag</td>'
            '<td class="actions"><a href="/dashboard/files/file-1/download"><button type="button">Download</button></a>'
            '<form><button>Delete</button></form></td></tr></tbody></table>'
        )
        rendered = ux._make_file_rows_clickable(html)
        self.assertIn('data-v971-file-href="/dashboard/files/file-1/download"', rendered)
        self.assertIn('<a class="v971-item-link" href="/dashboard/files/file-1/download">', rendered)
        self.assertIn(">Download<", rendered)
        self.assertNotIn(">View<", rendered)

    def test_source_preserves_project_scoped_existing_stores(self):
        source = inspect.getsource(ux)
        self.assertIn("context_engine().store.list_items", source)
        self.assertIn('project_id=project_id', source)
        self.assertIn("include_global=False", source)
        self.assertIn("Memories", source)
        self.assertIn("Skills", source)
        self.assertIn("Tasks and Files", source)
        self.assertNotIn("postmaster-mcp.yml", source)

    def test_runtime_installs_project_ux_after_mail_ux(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("install_mail_files_composer_v971(app"),
            source.index("install_projects_ux_v971(_webgui_v960"),
        )

    def test_composed_runtime_keeps_crud_routes_and_project_views(self):
        import postmaster.runtime as runtime
        from postmaster import webgui_v960 as v960
        from postmaster import webgui_v962 as v962

        self.assertIn("v971-file-row-navigation", v962.SCRIPT)
        self.assertTrue(getattr(v960, "_projects_ux_v971_installed", False))
        paths = {getattr(route, "path", "") for route in runtime.app.router.routes}
        for path in (
            "/dashboard/knowledge/save",
            "/dashboard/files/upload",
            "/dashboard/files/delete",
            "/dashboard/project/create",
            "/dashboard/project/update",
            "/dashboard/project/delete",
        ):
            self.assertIn(path, paths)


if __name__ == "__main__":
    unittest.main()
