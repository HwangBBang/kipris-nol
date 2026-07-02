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

    def test_banner_shown_without_key_hidden_with_key(self):
        from unittest import mock
        from kipris_nol import gui
        with mock.patch.object(gui.keystore, "load_key", return_value=None):
            app = gui.App()
            try:
                self.assertEqual(app._key_banner.winfo_manager(), "pack")
            finally:
                app.destroy()
        with mock.patch.object(gui.keystore, "load_key", return_value="K"):
            app = gui.App()
            try:
                self.assertEqual(app._key_banner.winfo_manager(), "")
            finally:
                app.destroy()

    def test_settings_verify_ok_saves_and_hides_banner(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, keystore
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(keystore, "config_dir", return_value=tmp):
            app = gui.App()
            try:
                dlg = app._open_settings()
                dlg._var.set("  MYKEY  ")
                dlg._vq.put(("ok", "MYKEY"))  # (검증 결과, 검증했던 키) — 검증한 키를 저장
                dlg._poll_verify()  # 큐에 결과가 있으므로 동기 1회로 처리됨
                self.assertEqual(keystore.load_key(), "MYKEY")
                self.assertIn("확인되었습니다", dlg._status.cget("text"))
                self.assertEqual(app._key_banner.winfo_manager(), "")
            finally:
                app.destroy()

    def test_settings_verify_auth30_does_not_save(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, keystore
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(keystore, "config_dir", return_value=tmp):
            app = gui.App()
            try:
                dlg = app._open_settings()
                dlg._var.set("WRONG")
                dlg._vq.put(("auth_30", "WRONG"))
                dlg._poll_verify()
                self.assertIsNone(keystore.load_key())
                self.assertIn("오류 30", dlg._status.cget("text"))
            finally:
                app.destroy()

    def test_settings_empty_key_disables_buttons(self):
        from unittest import mock
        from kipris_nol import gui
        with mock.patch.object(gui.keystore, "load_key", return_value=None):
            app = gui.App()
            try:
                dlg = app._open_settings()
                self.assertEqual(str(dlg._verify_btn.cget("state")), "disabled")
                dlg._var.set("X")
                self.assertEqual(str(dlg._verify_btn.cget("state")), "normal")
            finally:
                app.destroy()

    def test_auth_abort_writes_partial_and_warns(self):  # 설계 §6.4: 정상 파일명 오용 방지
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, viewmodel
        app = gui.App()
        try:
            tmp = Path(tempfile.mkdtemp())
            rows = [{"application_number": "40-1", "mark_name": "", "asset_status": "검토필요",
                     "account": "판단보류", "acquisition_cost": None, "basis": "인증오류",
                     "result_code": "30"}]
            with mock.patch.object(viewmodel, "default_output_dir", return_value=tmp), \
                 mock.patch.object(gui.messagebox, "showwarning") as warn, \
                 mock.patch.object(app, "_open_settings"):
                app._q.put(("auth_abort", rows))
                app._poll()
            self.assertTrue(list(tmp.glob("partial-ledger-*.csv")))
            self.assertFalse(list(tmp.glob("ledger-*.csv")))
            self.assertIn("인증 오류로 중단", app._banner.cget("text"))
            warn.assert_called_once()
        finally:
            app.destroy()

    def test_done_with_scattered_auth_error_writes_partial(self):  # cx-review 결정 1: 산발 1건도 partial-
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, viewmodel
        app = gui.App()
        try:
            tmp = Path(tempfile.mkdtemp())
            rows = [{"application_number": "40-1", "mark_name": "NOL", "asset_status": "등록",
                     "account": "상표권", "acquisition_cost": 1000.0, "basis": "x", "result_code": ""},
                    {"application_number": "40-2", "mark_name": "", "asset_status": "검토필요",
                     "account": "판단보류", "acquisition_cost": None, "basis": "인증오류",
                     "result_code": "31"}]
            with mock.patch.object(viewmodel, "default_output_dir", return_value=tmp), \
                 mock.patch.object(gui.messagebox, "showwarning") as warn, \
                 mock.patch.object(app, "_open_settings"):
                app._q.put(("done", rows, False))   # 중단 아님 — 정상 완료인데 31이 1건 섞임
                app._poll()
            self.assertTrue(list(tmp.glob("partial-ledger-*.csv")))
            self.assertFalse(list(tmp.glob("ledger-*.csv")))
            self.assertIn("만료(31)", warn.call_args.args[1])  # 결정 3: 31은 관리자 갱신 안내
        finally:
            app.destroy()

    def test_verify_result_after_dialog_close_still_saves(self):  # cx-review MUST 3 상향 버그 가드
        import tempfile
        from pathlib import Path
        from unittest import mock
        from kipris_nol import gui, keystore
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(keystore, "config_dir", return_value=tmp):
            app = gui.App()
            try:
                dlg = app._open_settings()
                poll = dlg._poll_verify
                dlg._vq.put(("ok", "K2"))
                dlg.destroy()          # 검증 대기 중 사용자가 다이얼로그를 닫음
                poll()                 # 앱 레벨 폴러 — 예외 없이 저장까지 수행돼야 함
                self.assertEqual(keystore.load_key(), "K2")
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
