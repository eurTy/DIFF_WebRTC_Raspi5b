"""Visible synthetic workcell; run with python -m pc_inspection.virtual_workcell_demo."""

import time
import tkinter as tk
from tkinter import ttk

from .virtual_station_sim import VirtualStationSim
from .virtual_workcell import SimulationControls, VirtualWorkcell


STATE_NAMES = {
    0: "等待工件",
    10: "传送中",
    20: "喷胶中",
    30: "周期完成",
    90: "故障锁定",
}
STATION_FAULTS = {
    0: "正常",
    10: "停止按钮",
    20: "防护门打开",
    30: "视觉资格丢失",
    40: "电机未就绪",
    50: "喷胶单元未就绪",
    60: "到位超时",
    70: "时间参数无效",
    80: "状态异常",
    90: "非合成源",
}
VISION_FAULTS = {
    0: "合格",
    10: "链路断开",
    20: "质量异常",
    30: "相机不可用",
    40: "区域未核验",
    50: "帧已过期",
    60: "序号停更",
    70: "等待新帧确认",
    90: "非合成源",
}


class VirtualWorkcellWindow:
    TICK_MS = 40

    def __init__(self):
        self.workcell = VirtualWorkcell()
        self.root = tk.Tk()
        self.root.title("虚拟工位 | 纯软件闭环")
        self.root.geometry("1320x780")
        self.root.minsize(1040, 650)
        self.root.configure(bg="#f3f5f6")
        self.started_ms = time.monotonic_ns() // 1_000_000
        self.pending = set()
        self.last_event = None

        self.camera_link = tk.BooleanVar(value=True)
        self.freeze_frames = tk.BooleanVar(value=False)
        self.guard_closed = tk.BooleanVar(value=True)
        self.motor_ready = tk.BooleanVar(value=True)
        self.glue_ready = tk.BooleanVar(value=True)
        self.arrival_sensor = tk.BooleanVar(value=True)
        self.station_text = tk.StringVar(value="等待工件")
        self.vision_text = tk.StringVar(value="等待新帧")
        self.conveyor_text = tk.StringVar(value="关闭")
        self.glue_text = tk.StringVar(value="关闭")
        self.sensor_text = tk.StringVar(value="未到位")
        self.sample_text = tk.StringVar(value="0")
        self.fault_text = tk.StringVar(value="无")
        self._build()
        self.root.after(self.TICK_MS, self._tick)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5f6")
        style.configure("TLabel", background="#f3f5f6", foreground="#25333a",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Header.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("State.TLabel", font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=8)
        style.configure("TCheckbutton", background="#f3f5f6",
                        font=("Microsoft YaHei UI", 10))

        shell = ttk.Frame(self.root, padding=18)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="虚拟传送与喷胶工位", style="Header.TLabel").pack(side="left")
        ttk.Label(header, text="纯软件模型 · 未连接 PLCSIM · 无物理输出").pack(side="right")

        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(left, bg="#e9eef0", highlightthickness=0,
                                width=900, height=490)
        self.canvas.pack(fill="both", expand=True)

        status = ttk.Frame(left)
        status.pack(fill="x", pady=(15, 0))
        for title, variable in (
            ("工序", self.station_text),
            ("视觉", self.vision_text),
            ("传送带", self.conveyor_text),
            ("喷胶", self.glue_text),
            ("到位", self.sensor_text),
        ):
            column = ttk.Frame(status)
            column.pack(side="left", fill="x", expand=True)
            ttk.Label(column, text=title).pack(anchor="w")
            ttk.Label(column, textvariable=variable,
                      style="State.TLabel").pack(anchor="w", pady=(3, 0))

        panel = ttk.Frame(body, padding=(22, 0, 0, 0))
        panel.pack(side="right", fill="y")
        ttk.Label(panel, text="仿真操作", style="State.TLabel").pack(anchor="w")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(12, 14))
        self.place_button = ttk.Button(actions, text="放入工件",
                                       command=self.workcell.place_part)
        self.place_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        self.start_button = ttk.Button(actions, text="启动周期",
                                       command=lambda: self._request("start"))
        self.start_button.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        self.stop_button = ttk.Button(actions, text="停止",
                                      command=lambda: self._request("stop"))
        self.stop_button.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.reset_button = ttk.Button(actions, text="复位",
                                       command=lambda: self._request("reset"))
        self.reset_button.grid(row=1, column=1, sticky="ew")
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        ttk.Separator(panel).pack(fill="x", pady=(4, 16))
        ttk.Label(panel, text="合成信号", style="State.TLabel").pack(anchor="w")
        for title, variable in (
            ("相机链路", self.camera_link),
            ("冻结画面", self.freeze_frames),
            ("防护门关闭", self.guard_closed),
            ("电机就绪", self.motor_ready),
            ("喷胶单元就绪", self.glue_ready),
            ("到位传感器有效", self.arrival_sensor),
        ):
            ttk.Checkbutton(panel, text=title, variable=variable).pack(
                anchor="w", pady=3)

        ttk.Separator(panel).pack(fill="x", pady=16)
        self._value_row(panel, "帧序号", self.sample_text)
        self._value_row(panel, "故障", self.fault_text)
        ttk.Label(panel, text="事件", style="State.TLabel").pack(
            anchor="w", pady=(17, 6))
        self.events = tk.Text(panel, width=33, height=8, wrap="word",
                              bg="#ffffff", fg="#25333a", relief="flat",
                              font=("Microsoft YaHei UI", 9), state="disabled")
        self.events.pack(fill="both", expand=True)

    def _value_row(self, parent, title, variable):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=title).pack(side="left")
        ttk.Label(row, textvariable=variable).pack(side="right")

    def _request(self, command):
        self.pending.add(command)

    def _controls(self):
        return SimulationControls(
            camera_link=self.camera_link.get(),
            freeze_frames=self.freeze_frames.get(),
            guard_closed=self.guard_closed.get(),
            motor_ready=self.motor_ready.get(),
            glue_ready=self.glue_ready.get(),
            arrival_sensor=self.arrival_sensor.get(),
        )

    def _record_event(self, snapshot):
        signature = (snapshot.station.sim_state, snapshot.station.sim_fault_code,
                     snapshot.vision.sim_fault_code)
        if signature == self.last_event:
            return
        self.last_event = signature
        elapsed = snapshot.now_ms / 1000
        line = (f"{elapsed:5.1f}s  {STATE_NAMES.get(snapshot.station.sim_state, '未知')}\n"
                f"视觉 {VISION_FAULTS.get(snapshot.vision.sim_fault_code, '未知')}"
                f"  ·  工序 {STATION_FAULTS.get(snapshot.station.sim_fault_code, '未知')}\n")
        self.events.configure(state="normal")
        self.events.insert("end", line)
        self.events.see("end")
        self.events.configure(state="disabled")

    def _render(self, snapshot):
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 300)
        h = max(c.winfo_height(), 250)
        scale = min(w / 900, h / 490)
        dx = (w - 900 * scale) / 2
        dy = (h - 490 * scale) / 2

        def p(x, y):
            return dx + x * scale, dy + y * scale

        def rect(x1, y1, x2, y2, **options):
            c.create_rectangle(*p(x1, y1), *p(x2, y2), **options)

        def line(x1, y1, x2, y2, **options):
            c.create_line(*p(x1, y1), *p(x2, y2), **options)

        def label(x, y, value, **options):
            c.create_text(*p(x, y), text=value, font=("Microsoft YaHei UI", 11),
                          **options)

        rect(0, 0, 900, 490, fill="#e9eef0", outline="")
        rect(0, 365, 900, 490, fill="#dce4e7", outline="")
        line(0, 365, 900, 365, fill="#afc0c6", width=2)
        label(52, 37, "合成工位 A / B", anchor="w", fill="#344a52")
        label(848, 37, "软件闭环", anchor="e", fill="#607780")

        # Camera and virtual detection zone
        rect(182, 77, 282, 130, fill="#324a53", outline="")
        rect(216, 121, 248, 142, fill="#243840", outline="")
        c.create_oval(*p(222, 126), *p(242, 146), fill="#7ad3c2", outline="")
        line(232, 146, 232, 250, fill="#4cb3a0", width=2, dash=(5, 5))
        label(232, 59, "合成视觉", fill="#344a52")
        label(232, 230, "检测区", fill="#388e80")

        # Glue nozzle and arrival sensor
        rect(646, 76, 700, 150, fill="#73838b", outline="#566870", width=2)
        rect(659, 150, 687, 184, fill="#364e57", outline="")
        c.create_polygon(*p(661, 184), *p(685, 184), *p(673, 203),
                         fill="#b59c3a", outline="")
        line(673, 204, 673, 303, fill="#b59c3a", width=2, dash=(3, 6))
        label(673, 59, "喷胶位", fill="#344a52")
        sensor_color = ("#148d77" if snapshot.station_inputs.at_glue_station
                        else "#8da3a9")
        c.create_oval(*p(659, 343), *p(687, 371),
                      fill=sensor_color, outline="#ffffff", width=2)

        # Conveyor, rollers and supports
        rect(60, 285, 842, 360, fill="#405860", outline="#283c44", width=2)
        rect(60, 284, 842, 301, fill="#263c45", outline="")
        offset = snapshot.belt_phase * 58
        for index in range(-1, 15):
            x = 75 + index * 58 + offset
            if 68 <= x <= 820:
                line(x, 293, x + 20, 293, fill="#8ba4aa", width=3)
        for index in range(12):
            x = 93 + index * 65
            c.create_oval(*p(x - 17, 320), *p(x + 17, 350),
                          fill="#9aadb2", outline="#273e46", width=2)
        for x in (130, 730):
            rect(x, 360, x + 30, 424, fill="#617981", outline="")
        label(84, 400, "上料", anchor="w", fill="#526970")
        label(814, 400, "出料", anchor="e", fill="#526970")

        if snapshot.part_position is not None:
            x = 60 + 782 * snapshot.part_position
            rect(x - 34, 242, x + 34, 284, fill="#d7a15d",
                 outline="#855b30", width=2)
            rect(x - 24, 251, x + 24, 264, fill="#f1c98e", outline="")
            if snapshot.glue_applied:
                line(x - 20, 246, x + 20, 246, fill="#198c7b", width=5)
            label(x, 221, "工件", fill="#684929")

        if snapshot.station.sim_glue_cmd:
            for index in range(4):
                drop_y = 218 + ((snapshot.now_ms // 12 + index * 25) % 75)
                c.create_oval(*p(670, drop_y), *p(676, drop_y + 9),
                              fill="#1ba78c", outline="")

        belt_color = "#168b79" if snapshot.station.sim_conveyor_cmd else "#82979e"
        glue_color = "#168b79" if snapshot.station.sim_glue_cmd else "#82979e"
        c.create_oval(*p(63, 435), *p(78, 450), fill=belt_color, outline="")
        label(86, 443, "传送命令", anchor="w", fill="#344a52")
        c.create_oval(*p(218, 435), *p(233, 450), fill=glue_color, outline="")
        label(241, 443, "喷胶命令", anchor="w", fill="#344a52")

    def _tick(self):
        now_ms = (time.monotonic_ns() // 1_000_000) - self.started_ms
        pending, self.pending = self.pending, set()
        snapshot = self.workcell.tick(
            now_ms,
            controls=self._controls(),
            start="start" in pending,
            stop="stop" in pending,
            reset="reset" in pending,
        )
        self.station_text.set(STATE_NAMES.get(snapshot.station.sim_state, "未知"))
        self.vision_text.set(VISION_FAULTS.get(snapshot.vision.sim_fault_code, "未知"))
        self.conveyor_text.set("运行" if snapshot.station.sim_conveyor_cmd else "停止")
        self.glue_text.set("运行" if snapshot.station.sim_glue_cmd else "停止")
        self.sensor_text.set("已到位" if snapshot.station_inputs.at_glue_station
                             else "未到位")
        self.sample_text.set(str(snapshot.sample_seq))
        self.fault_text.set(STATION_FAULTS.get(snapshot.station.sim_fault_code, "未知"))
        self.place_button.state(["!disabled"] if (snapshot.station.sim_state == 0
                           and snapshot.part_position is None) else ["disabled"])
        ready_to_start = (snapshot.station.sim_state == 0
                          and snapshot.part_position is not None
                          and snapshot.vision.sim_motion_observed
                          and self.guard_closed.get() and self.motor_ready.get()
                          and self.glue_ready.get())
        self.start_button.state(["!disabled"] if ready_to_start else ["disabled"])
        self.stop_button.state(["!disabled"] if snapshot.station.sim_state in (10, 20)
                               else ["disabled"])
        self.reset_button.state(["!disabled"] if snapshot.station.sim_state in (30, 90)
                                else ["disabled"])
        self._record_event(snapshot)
        self._render(snapshot)
        self.root.after(self.TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


def main():
    VirtualWorkcellWindow().run()


if __name__ == "__main__":
    main()
