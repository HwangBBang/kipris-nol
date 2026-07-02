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
        ttk.Button(top, text="⚙ 설정(accessKey)", command=self._open_settings).pack(side="left")
        ttk.Button(top, text="CSV 파일 열기", command=self._load_csv).pack(side="left", padx=4)
        self._count = ttk.Label(top, text="인식된 0건")
        self._count.pack(side="right")

        ttk.Label(self, text="엑셀에서 [출원번호][취득원가] 두 열을 복사해 아래에 붙여넣으세요:").pack(
            anchor="w", padx=8)
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

    def _refresh_count(self) -> None:
        n = len(inputs.parse_pasted(self._paste.get("1.0", "end")))
        self._count.config(text=f"인식된 {n}건")
        self._run_btn.config(state=("normal" if n > 0 else "disabled"))  # Q-1a

    def _counts_text(self) -> str:
        order = ["등록", "대기", "탈락", "검토필요", "unsupported"]
        return " · ".join(f"{b} {self._counts[b]}" for b in order if b in self._counts)

    def _open_settings(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("설정 — KIPRIS accessKey")
        dlg.transient(self)
        ttk.Label(dlg, text="KIPRIS Plus accessKey:", padding=8).pack(anchor="w")
        var = tk.StringVar(value=keystore.load_key() or "")
        ent = ttk.Entry(dlg, textvariable=var, show="•", width=52)
        ent.pack(padx=8, fill="x")

        def _save():
            keystore.save_key(var.get())
            dlg.destroy()

        ttk.Button(dlg, text="저장", command=_save).pack(pady=8)
        ent.focus_set()

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
            )
            self._q.put(("done", rows, self._cancel.is_set()))   # MUST-2: 취소 여부 동반
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

    def _finish(self, rows, cancelled=False) -> None:  # MUST-2: 취소 시 partial- 저장
        self._reset_run()
        self._cur.config(text="")
        out_dir = viewmodel.default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = "partial-" if cancelled else ""   # 완성본 파일명으로의 조용한 저장 금지
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
        self._banner.config(text=banner)
        auth_err = sum(1 for r in rows if r.get("result_code") in config.FATAL_RESULT_CODES)
        if auth_err:
            messagebox.showwarning(
                "인증 오류", f"정보검색 인증오류 {auth_err}건 — accessKey 신청/갱신 상태를 확인하세요.")
            self._open_settings()

    def _reset_run(self) -> None:
        self._run_btn.config(state="normal")
        self._cancel_btn.config(state="disabled")

    def _open_out(self) -> None:
        if self._out_dir and hasattr(os, "startfile"):
            os.startfile(self._out_dir)  # type: ignore[attr-defined]  # Windows 전용
