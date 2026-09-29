"""UI construction smoke test. No login, network, tray or real local account data."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'nt', 'Windows desktop only')
class UITests(unittest.TestCase):
    def test_window_and_plan_editor_construct_without_network(self):
        import tkinter as tk
        from sports_szu.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                with patch.object(App, 'worker'), patch.object(App, 'setup_tray'):
                    app = App(root, Path(directory))
                app.plan_editor()
                root.update_idletasks()
                self.assertEqual(len(app.store.plans()), 0)
                windows = [x for x in root.winfo_children() if isinstance(x, tk.Toplevel)]
                self.assertEqual(len(windows), 1)
                form = windows[0].winfo_children()[0]
                save_button = form.winfo_children()[-1]
                self.assertLessEqual(save_button.winfo_y() + save_button.winfo_height(), form.winfo_height())
                app.store.close()
                app.stop.set()
            finally:
                root.destroy()
