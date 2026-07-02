"""KIPRIS-NOL 데스크탑 GUI(tkinter). 로직은 engine/inputs/keystore/viewmodel 위임 — 여긴 배선만."""
from __future__ import annotations

import os
import queue
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import accounting, config, core, engine, inputs, keystore, viewmodel


def main() -> None:
    App().mainloop()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KIPRIS-NOL — 회계 자산 분류")
        self.geometry("960x720")
        self._q: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._out_dir: Path | None = None
        self._counts: dict = {}
        self._build()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self._key_banner = tk.Frame(self, bg="#fff3cd")  # 키 미등록 안내(설계 §6.1) — ttk는 bg 불가라 tk
        tk.Label(self._key_banner, bg="#fff3cd",
                 text="관리자에게 전달받은 KIPRIS 인증키를 등록해 주세요. 분류를 실행하려면 키가 필요합니다."
                 ).pack(side="left", padx=8, pady=6)
        ttk.Button(self._key_banner, text="키 등록하기", command=self._open_settings).pack(side="left", padx=8)
        ttk.Button(top, text="⚙ 설정(accessKey)", command=self._open_settings).pack(side="left")
        ttk.Button(top, text="CSV 파일 열기", command=self._load_csv).pack(side="left", padx=4)
        self._count = ttk.Label(top, text="인식된 0건")
        self._count.pack(side="right")

        self._instr = ttk.Label(self, text="엑셀에서 [출원번호][취득원가] 두 열을 복사해 아래에 붙여넣으세요:")
        self._instr.pack(anchor="w", padx=8)
        self._paste = tk.Text(self, height=10)
        self._paste.pack(fill="x", padx=8)
        self._paste.bind("<KeyRelease>", lambda e: self._refresh_count())
        self._paste.bind("<<Paste>>", lambda e: self.after(10, self._refresh_count))

        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="x")
        self._run_btn = ttk.Button(mid, text="분류 실행", command=self._run)
        self._run_btn.pack(side="left")
        self._cancel_btn = ttk.Button(mid, text="취소", command=lambda: self._cancel.set(),
                                      state="disabled")
        self._cancel_btn.pack(side="left", padx=4)
        self._open_btn = ttk.Button(mid, text="결과 폴더 열기", command=self._open_out,
                                    state="disabled")
        self._open_btn.pack(side="left", padx=4)
        self._bar = ttk.Progressbar(mid, mode="determinate")
        self._bar.pack(side="left", fill="x", expand=True, padx=8)

        self._cur = ttk.Label(self, text="", padding=(8, 0))          # 결정3: 현재 처리 항목
        self._cur.pack(anchor="w")
        self._counts_lbl = ttk.Label(self, text="", padding=(8, 0))   # 결정3: 실시간 카운트
        self._counts_lbl.pack(anchor="w")
        self._err_lbl = ttk.Label(self, text="", padding=(8, 0), foreground="#b00")  # 결정3: 에러 1줄
        self._err_lbl.pack(anchor="w")

        self._banner = ttk.Label(self, text="", padding=(8, 4))
        self._banner.pack(anchor="w")

        cols = viewmodel.DISPLAY_COLUMNS
        self._tree = ttk.Treeview(self, columns=cols, show="headings")
        for c in cols:
            self._tree.heading(c, text=c)
            self._tree.column(c, width=150, anchor="w")
        self._tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._refresh_count()  # Q-1a: 초기 0건 → [분류 실행] 비활성
        self._refresh_key_banner()

    def _refresh_key_banner(self) -> None:
        if keystore.load_key():
            self._key_banner.pack_forget()
        else:
            self._key_banner.pack(fill="x", padx=8, pady=(0, 4), before=self._instr)

    def _refresh_count(self) -> None:
        n = len(inputs.parse_pasted(self._paste.get("1.0", "end")))
        self._count.config(text=f"인식된 {n}건")
        self._run_btn.config(state=("normal" if n > 0 else "disabled"))  # Q-1a

    def _counts_text(self) -> str:
        order = ["등록", "대기", "탈락", "검토필요", "unsupported"]
        return " · ".join(f"{b} {self._counts[b]}" for b in order if b in self._counts)

    def _open_settings(self) -> tk.Toplevel:
        dlg = tk.Toplevel(self)
        dlg.title("KIPRIS 인증키 등록")
        dlg.transient(self)
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="관리자에게 전달받은 KIPRIS 인증키를 아래에 붙여넣고 [확인 후 저장]을 누르세요."
                  ).pack(anchor="w")
        ttk.Label(frm, text="키는 이 PC의 내 계정 폴더에만 저장됩니다.", foreground="#666"
                  ).pack(anchor="w", pady=(0, 8))

        var = tk.StringVar(value=keystore.load_key() or "")
        row = ttk.Frame(frm)
        row.pack(fill="x")
        ent = ttk.Entry(row, textvariable=var, show="•", width=52)
        ent.pack(side="left", fill="x", expand=True)
        shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="키 표시", variable=shown,
                        command=lambda: ent.config(show="" if shown.get() else "•")
                        ).pack(side="left", padx=4)

        menu = tk.Menu(ent, tearoff=0)  # 우클릭 붙여넣기(비개발자 동선). Ctrl+V는 Tk 기본 바인딩 유지
        menu.add_command(label="붙여넣기", command=lambda: ent.event_generate("<<Paste>>"))
        ent.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

        status = ttk.Label(frm, text="", wraplength=430, padding=(0, 8))
        status.pack(anchor="w")
        btns = ttk.Frame(frm)
        btns.pack(anchor="e")
        vq: queue.Queue = queue.Queue()

        def _set_status(text: str, color: str) -> None:
            status.config(text=text, foreground=color)

        def _sync(*_) -> None:
            st = "normal" if var.get().strip() else "disabled"
            verify_btn.config(state=st)
            plain_btn.config(state=st)

        def _poll_verify() -> None:
            # 앱 레벨(self.after) 폴링 — 다이얼로그가 닫혀도 저장은 수행된다(cx-review MUST 3 해소).
            try:
                result, key = vq.get_nowait()  # (결과, 검증했던 키) — 검증한 키 그대로 저장(레이스 차단)
            except queue.Empty:
                self.after(100, _poll_verify)
                return
            saved = result in viewmodel.VERIFY_SAVE_OK
            if saved:
                keystore.save_key(key)
                self._refresh_key_banner()
            if dlg.winfo_exists():  # 위젯 갱신만 다이얼로그 수명에 종속
                color = "#060" if result == "ok" else ("#b60" if saved else "#b00")
                _set_status(viewmodel.verify_message(result), color)
                ent.config(state="normal")
                _sync()

        def _verify_save() -> None:
            key = var.get().strip()
            verify_btn.config(state="disabled")
            plain_btn.config(state="disabled")
            ent.config(state="disabled")  # 검증 중 편집 차단 — 검증한 키 = 저장할 키 보장
            _set_status("키 확인 중… (최대 40초)", "#444")
            threading.Thread(target=lambda: vq.put((engine.verify_key(key), key)), daemon=True).start()
            self.after(100, _poll_verify)

        def _save_plain() -> None:
            keystore.save_key(var.get())
            self._refresh_key_banner()
            _set_status("저장했습니다. (키 확인은 하지 않았습니다 — 실행에서 오류가 나면 관리자에게 문의하세요)", "#b60")

        verify_btn = ttk.Button(btns, text="확인 후 저장", command=_verify_save)
        verify_btn.pack(side="left")
        plain_btn = ttk.Button(btns, text="확인 없이 저장", command=_save_plain)
        plain_btn.pack(side="left", padx=4)
        var.trace_add("write", _sync)
        _sync()
        ent.focus_set()
        # 기본 버튼: 붙여넣기 → Enter 동선(설계 §6.2 "[확인 후 저장] (기본 버튼)")
        dlg.bind("<Return>", lambda e: _verify_save()
                 if str(verify_btn.cget("state")) == "normal" else None)
        # Windows CI 테스트 훅
        dlg._var, dlg._ent, dlg._status = var, ent, status
        dlg._verify_btn, dlg._plain_btn = verify_btn, plain_btn
        dlg._vq, dlg._poll_verify = vq, _poll_verify
        return dlg

    def _load_csv(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")])
        if not p:
            return
        entries = inputs.parse_csv(Path(p).read_bytes())
        text = "\n".join(f"{e['application_number']}\t{e.get('cost') or ''}" for e in entries)
        self._paste.delete("1.0", "end")
        self._paste.insert("1.0", text)
        self._refresh_count()

    def _run(self) -> None:
        key = keystore.load_key()
        if not key:
            messagebox.showinfo("설정 필요", "먼저 KIPRIS accessKey를 입력하세요.")
            self._open_settings()
            return
        entries = inputs.parse_pasted(self._paste.get("1.0", "end"))
        if not entries:
            messagebox.showwarning("입력 없음", "인식된 출원번호가 없습니다. 붙여넣기를 확인하세요.")
            return
        self._cancel.clear()
        self._run_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._open_btn.config(state="disabled")
        self._bar.config(maximum=len(entries), value=0)
        self._tree.delete(*self._tree.get_children())
        self._counts = {}
        self._cur.config(text="")
        self._counts_lbl.config(text="")
        self._err_lbl.config(text="")
        self._banner.config(text="분류 중…")
        self._worker = threading.Thread(target=self._work, args=(entries, key), daemon=True)
        self._worker.start()
        self.after(100, self._poll)

    def _work(self, entries, key) -> None:
        try:
            rows = engine.classify_entries(
                entries, key, source="c", delay=config.INTER_CALL_DELAY_SEC,
                progress_cb=lambda idx, total, appno, row: self._q.put(("progress", idx, total, appno, row)),
                should_cancel=self._cancel.is_set,
                auth_abort_threshold=config.AUTH_ABORT_THRESHOLD,
            )
            self._q.put(("done", rows, self._cancel.is_set()))   # MUST-2: 취소 여부 동반
        except engine.AuthAbortError as exc:  # 연속 인증오류 — 부분 결과는 partial-로 저장
            self._q.put(("auth_abort", exc.rows))
        except core.FatalAuthError as exc:  # C-모드에선 드묾(방어)
            self._q.put(("fatal", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self._q.put(("error", core._scrub(exc, key)))

    def _poll(self) -> None:
        terminal = False
        try:
            while True:
                msg = self._q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, idx, total, appno, row = msg
                    self._bar.config(value=idx)
                    self._cur.config(text=f"처리 중 {idx}/{total}: {appno}")
                    st = row["asset_status"]
                    self._counts[st] = self._counts.get(st, 0) + 1
                    self._counts_lbl.config(text=self._counts_text())
                    if st == "검토필요":
                        self._err_lbl.config(text=f"검토필요: {appno} — {(row.get('basis') or '')[:50]}")
                elif kind == "done":
                    self._finish(msg[1], msg[2])   # rows, cancelled
                    terminal = True
                elif kind == "auth_abort":
                    self._finish(msg[1], auth_aborted=True)
                    terminal = True
                elif kind == "fatal":
                    self._reset_run()
                    messagebox.showerror("인증 오류", f"{msg[1]}\naccessKey를 확인하세요.")
                    self._open_settings()
                    terminal = True
                elif kind == "error":
                    self._reset_run()
                    messagebox.showerror("오류", msg[1])
                    terminal = True
        except queue.Empty:
            pass
        if not terminal:
            self.after(100, self._poll)

    def _finish(self, rows, cancelled=False, auth_aborted=False) -> None:  # MUST-2: 부분본은 partial-
        self._reset_run()
        self._cur.config(text="")
        n30 = sum(1 for r in rows if r.get("result_code") == "30")
        n31 = sum(1 for r in rows if r.get("result_code") == "31")
        auth_err = sum(1 for r in rows if r.get("result_code") in config.FATAL_RESULT_CODES)  # partial- 판정은 config 기준(이중 소스 방지)
        out_dir = viewmodel.default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # cx-review 결정 1: 인증오류가 1건이라도 있으면 완성본 파일명 금지
        prefix = "partial-" if (cancelled or auth_aborted or auth_err) else ""
        accounting.write_ledger_csv(rows, out_dir / f"{prefix}ledger-{stamp}.csv")
        accounting.write_review_csv(rows, out_dir / f"{prefix}review-{stamp}.csv")
        self._out_dir = out_dir
        self._open_btn.config(state="normal")
        self._tree.delete(*self._tree.get_children())
        for cells in viewmodel.result_rows(rows):
            self._tree.insert("", "end", values=cells)
        banner = viewmodel.summary_banner(rows)
        if cancelled:
            banner = f"취소됨: {len(rows)}건만 수집 (partial- 파일로 저장) · " + banner
        elif auth_aborted:
            banner = f"인증 오류로 중단: {len(rows)}건만 수집 (partial- 파일로 저장) · " + banner
        elif auth_err:
            banner = f"인증 오류 {auth_err}건 포함 (partial- 파일로 저장) · " + banner
        self._banner.config(text=banner)
        if auth_err:
            head = "인증 오류로 중단했습니다.\n" if auth_aborted else ""  # 설계 §6.4 확정 문구
            lines = []  # cx-review 결정 3: 해결 주체가 다른 30/31을 분리 안내
            if n30:
                lines.append(f"키 미등록 오류(30) {n30}건 — 키를 재확인해 다시 입력하고, 계속되면 관리자에게 문의하세요.")
            if n31:
                lines.append(f"키 사용 기간 만료(31) {n31}건 — 관리자에게 갱신을 요청하세요.")
            messagebox.showwarning("인증 오류", head + "\n".join(lines))
            self._open_settings()

    def _reset_run(self) -> None:
        self._run_btn.config(state="normal")
        self._cancel_btn.config(state="disabled")

    def _open_out(self) -> None:
        if self._out_dir and hasattr(os, "startfile"):
            os.startfile(self._out_dir)  # type: ignore[attr-defined]  # Windows 전용
