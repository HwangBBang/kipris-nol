import unittest

try:
    import tkinter  # noqa: F401
    _root = tkinter.Tk()  # 헤드리스(디스플레이 없음)면 TclError
    _root.destroy()
    _HAS_TK = True
except Exception:
    _HAS_TK = False


@unittest.skipUnless(_HAS_TK, "tkinter/디스플레이 없음 — Windows CI에서 실행")
class TestGuiSmoke(unittest.TestCase):
    def test_app_builds_and_destroys(self):
        from kipris_nol import gui
        app = gui.App()
        try:
            app.update_idletasks()
            self.assertEqual(app.title(), "KIPRIS-NOL — 회계 자산 분류")
        finally:
            app.destroy()

    def test_recognized_count_updates_from_paste(self):
        from kipris_nol import gui
        app = gui.App()
        try:
            app._paste.insert("1.0", "40-2025-0233236\t1000\n70-2024-0001232\t2000")
            app._refresh_count()
            self.assertIn("2건", app._count.cget("text"))
        finally:
            app.destroy()

    def test_run_button_disabled_when_empty(self):  # Q-1a
        from kipris_nol import gui
        app = gui.App()
        try:
            app._refresh_count()
            self.assertEqual(str(app._run_btn.cget("state")), "disabled")
        finally:
            app.destroy()

    def test_done_writes_full_and_reenables(self):  # SHOULD-4 + MUST-2(정상 완료 경로)
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, viewmodel
        app = gui.App()
        try:
            tmp = Path(tempfile.mkdtemp())
            rows = [{"application_number": "40-1", "mark_name": "NOL", "asset_status": "등록",
                     "account": "상표권", "acquisition_cost": 1000.0, "basis": "x", "result_code": ""}]
            with mock.patch.object(viewmodel, "default_output_dir", return_value=tmp):
                app._q.put(("done", rows, False))
                app._poll()  # terminal → 재폴링 없음
            self.assertEqual(str(app._run_btn.cget("state")), "normal")
            self.assertTrue(list(tmp.glob("ledger-*.csv")))
            self.assertFalse(list(tmp.glob("partial-*.csv")))
        finally:
            app.destroy()

    def test_cancel_writes_partial_prefix(self):  # MUST-2: 취소 부분본은 partial- 파일명
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, viewmodel
        app = gui.App()
        try:
            tmp = Path(tempfile.mkdtemp())
            rows = [{"application_number": "40-1", "mark_name": "NOL", "asset_status": "등록",
                     "account": "상표권", "acquisition_cost": 1000.0, "basis": "x", "result_code": ""}]
            with mock.patch.object(viewmodel, "default_output_dir", return_value=tmp):
                app._q.put(("done", rows, True))
                app._poll()
            self.assertTrue(list(tmp.glob("partial-ledger-*.csv")))
            self.assertFalse(list(tmp.glob("ledger-*.csv")))  # 완성본 파일명으로 저장 안 함
            self.assertIn("취소", app._banner.cget("text"))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
