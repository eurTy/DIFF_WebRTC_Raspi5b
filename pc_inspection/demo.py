"""Local inspection-cell demonstration; run with python -m pc_inspection.demo."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

from .core import AssemblyFixture, SCENARIOS, SLOTS, SimulatedCell, SlotDetector, clock_ms


LABELS = {
    "OK": "全部到位",
    "SLOT_2_MISSING": "2 号缺件",
    "SLOT_3_MISPLACED": "3 号错位",
    "SLOT_1_OCCLUDED": "1 号遮挡",
}


def annotated_image(image, statuses):
    canvas = image.copy()
    for i, (region, status) in enumerate(zip(SLOTS, statuses), 1):
        x, y, w, h = region.rect(canvas.shape[1], canvas.shape[0])
        color = {"PRESENT": (107, 168, 44), "MISSING": (70, 70, 217),
                 "MISPLACED": (70, 70, 217), "UNKNOWN": (28, 182, 230)}[status]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 4)
        cv2.putText(canvas, f"SLOT {i}: {status}", (x, y - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, .68, color, 2, cv2.LINE_AA)
    cv2.putText(canvas, "SYNTHETIC INPUT | SIMULATED OUTPUT", (48, 70),
                cv2.FONT_HERSHEY_SIMPLEX, .9, (236, 240, 241), 2, cv2.LINE_AA)
    return canvas


class InspectionWindow:
    def __init__(self):
        self.fixture = AssemblyFixture()
        self.detector = SlotDetector(self.fixture.empty)
        self.cell = SimulatedCell()
        self.frame_id = 0
        self.pending = None
        self.last_event = None
        self.log_path = (Path(__file__).resolve().parents[1] / "private" /
                         f"inspection-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")
        self.root = tk.Tk()
        self.root.title("三工位装配防错 | 电脑端原型")
        self.root.configure(bg="#eef2f2")
        self.root.geometry("1100x610")
        self.root.minsize(1050, 580)
        self.scenario = tk.StringVar(value=LABELS["OK"])
        self.state = tk.StringVar(value="等待稳定识别")
        self.reason = tk.StringVar(value="合成测试，未连接 PLCSIM 或设备")
        self.age = tk.StringVar(value="-- ms")
        self.conveyor = tk.StringVar(value="关闭")
        self.glue = tk.StringVar(value="关闭")
        self.slots = [tk.StringVar(value="待判定") for _ in SLOTS]
        self._build()
        self.root.after(100, self._step)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f2")
        style.configure("TLabel", background="#eef2f2", foreground="#1d2e34",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Value.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=8)
        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="三工位装配防错", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="离线合成画面 · 软件模拟 PLC · 无物理输出").pack(side="right")
        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        self.preview = ttk.Label(body)
        self.preview.pack(side="left", anchor="n")
        panel = ttk.Frame(body, padding=(22, 0, 0, 0))
        panel.pack(side="left", fill="both", expand=True)
        ttk.Label(panel, text="样本场景").pack(anchor="w")
        picker = ttk.Combobox(panel, textvariable=self.scenario, state="readonly",
                              values=list(LABELS.values()), width=22)
        picker.pack(anchor="w", fill="x", pady=(5, 18))
        ttk.Label(panel, text="模拟流程").pack(anchor="w")
        ttk.Label(panel, textvariable=self.state, style="Value.TLabel").pack(anchor="w", pady=(4, 5))
        ttk.Label(panel, textvariable=self.reason, wraplength=265).pack(anchor="w", pady=(0, 15))
        for i, status in enumerate(self.slots, 1):
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"工位 {i}").pack(side="left")
            ttk.Label(row, textvariable=status, style="Value.TLabel").pack(side="right")
        ttk.Separator(panel).pack(fill="x", pady=16)
        for title, variable in (("传送带", self.conveyor), ("喷胶", self.glue),
                                ("判定帧龄", self.age)):
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=title).pack(side="left")
            ttk.Label(row, textvariable=variable, style="Value.TLabel").pack(side="right")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(23, 0))
        ttk.Button(actions, text="启动周期", command=lambda: self._request("start")).pack(
            side="left", padx=(0, 6))
        ttk.Button(actions, text="复位", command=lambda: self._request("reset")).pack(
            side="left", padx=(0, 6))
        ttk.Button(actions, text="停止", command=lambda: self._request("stop")).pack(side="left")
        ttk.Label(panel, text="状态变化记录在仓库 private/ 目录", wraplength=265).pack(
            anchor="w", pady=(18, 0))

    def _request(self, command):
        self.pending = command

    def _record(self, scenario, inspection, output, now_ms):
        signature = (scenario, inspection.statuses, output.state, output.reason)
        if signature == self.last_event:
            return
        self.last_event = signature
        event = {
            "wall_time": datetime.now().astimezone().isoformat(),
            "source": inspection.source,
            "scenario": scenario,
            "frame_id": inspection.frame_id,
            "captured_ms": inspection.captured_ms,
            "decided_ms": inspection.decided_ms,
            "age_ms": round(inspection.age_ms(now_ms), 2),
            "slots": dict(zip((r.name for r in SLOTS), inspection.statuses)),
            "cell_state": output.state,
            "conveyor": output.conveyor,
            "glue": output.glue,
            "reason": output.reason,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _step(self):
        scenario = next(key for key, label in LABELS.items() if label == self.scenario.get())
        self.frame_id += 1
        captured_ms = clock_ms()
        image = self.fixture.frame(scenario)
        inspection = self.detector.inspect(image, self.frame_id, captured_ms)
        now_ms = clock_ms()
        command, self.pending = self.pending, None
        output = self.cell.tick(inspection, now_ms, start=command == "start",
                                reset=command == "reset", stop=command == "stop")
        self._record(scenario, inspection, output, now_ms)
        view = annotated_image(image, inspection.statuses)
        view = cv2.resize(view, (720, 405), interpolation=cv2.INTER_AREA)
        self.photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(view, cv2.COLOR_BGR2RGB)))
        self.preview.configure(image=self.photo)
        display_state = {"BLOCKED": "禁止启动", "READY": "就绪", "MOVE": "传送带运行",
                         "GLUE": "模拟喷胶", "COMPLETE": "周期完成", "FAULT": "故障锁定"}
        display_status = {"PRESENT": "到位", "MISSING": "缺件", "MISPLACED": "错位",
                          "UNKNOWN": "待确认"}
        self.state.set(display_state[output.state])
        self.reason.set(output.reason or "视觉条件满足")
        for variable, status in zip(self.slots, inspection.statuses):
            variable.set(display_status[status])
        self.conveyor.set("运行" if output.conveyor else "关闭")
        self.glue.set("运行" if output.glue else "关闭")
        self.age.set(f"{inspection.age_ms(now_ms):.1f} ms")
        self.root.after(100, self._step)

    def run(self):
        self.root.mainloop()


def headless_check():
    fixture = AssemblyFixture()
    for scenario in SCENARIOS:
        detector = SlotDetector(fixture.empty)
        inspection = None
        for frame_id in range(1, 4):
            inspection = detector.inspect(fixture.frame(scenario), frame_id, 1000, 1000)
        cell = SimulatedCell()
        cell.tick(inspection, 1000)
        output = cell.tick(inspection, 1000, start=True)
        print(f"{scenario}: {inspection.statuses} -> {output.state}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Run the synthetic smoke test")
    args = parser.parse_args()
    if args.headless:
        headless_check()
    else:
        InspectionWindow().run()


if __name__ == "__main__":
    main()
