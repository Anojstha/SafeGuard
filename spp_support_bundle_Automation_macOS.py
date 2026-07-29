#!/usr/bin/env python3

"""SafeGuard Support Bundle Automation - final consolidated version."""

 

import ctypes

import datetime as dt

import json

import os

import queue

import re

import shutil

import subprocess

import sys

import threading

from pathlib import Path

from urllib.parse import quote, urlencode, urlparse

 

import tkinter as tk

from tkinter import filedialog, font as tkfont, messagebox, ttk

 

APP_TITLE = "SafeGuard Support Bundle Automation"

API_PATH = "/service/appliance/v4/SupportBundle"

PART_SIZE_MB = 700

PART_SIZE_BYTES = PART_SIZE_MB * 1024 * 1024

LOCK_FILE = Path.home() / ".spp_support_bundle_collection.lock"

SFTP_HOST = "sft.schwab.com"

SFTP_PORT = 22

SFTP_USER = "OneIdentity-SCS"

SFTP_ROOT = "/Outbound"

WZZIP_DEFAULT = r"C:\Program Files\WinZip\WZZIP.EXE"

TIERS = ("Tier 0", "Tier 1")

ENVIRONMENTS = ("Dev", "Pre-Prod", "Prod")

INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

 

class AppError(Exception):

    pass

 

def enable_dpi_awareness():

    if not sys.platform.startswith("win"):

        return

    for callback in (

        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)),

        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),

        lambda: ctypes.windll.user32.SetProcessDPIAware(),

    ):

        try:

            callback()

            return

        except Exception:

            pass

 

class SupportBundleApp:

    def __init__(self, root):

        self.root = root

        self.root.title(APP_TITLE)

        self.worker = None

        self.current_process = None

        self.stop_event = threading.Event()

        self.log_queue = queue.Queue()

        self.url_widgets = {}

        self.monitor_handle = None

        self.monitor_job = None

 

        self.case_number = tk.StringVar()

        self.tier = tk.StringVar(value="Tier 0")

        self.environment = tk.StringVar(value="Dev")

        self.days = tk.StringVar(value="7")

        self.limit_logs = tk.BooleanVar(value=True)

        self.include_events = tk.BooleanVar(value=False)

        self.output_root = tk.StringVar()

        self.api_path = tk.StringVar(value=API_PATH)

        self.wzzip_path = tk.StringVar(value=self.find_wzzip() or WZZIP_DEFAULT)

        self.delete_original = tk.BooleanVar(value=False)

        self.auto_upload = tk.BooleanVar(value=True)

        self.sftp_host = tk.StringVar(value=SFTP_HOST)

        self.sftp_port = tk.StringVar(value=str(SFTP_PORT))

        self.sftp_user = tk.StringVar(value=SFTP_USER)

        self.sftp_root = tk.StringVar(value=SFTP_ROOT)

        self.winscp_path = tk.StringVar(value=self.find_winscp() or "")

 

        self.apply_monitor_layout(initial=True)

        self.build_ui()

        self.update_selected_target()

        self.toggle_days()

        self.poll_logs()

        self.monitor_job = self.root.after(600, self.poll_monitor)

        self.root.after(300, self.detect_previous)

        self.log(f"Preferred WinZip command-line executable: {self.wzzip_path.get()}")

 

    # ---------------- Monitor and DPI ----------------

    def monitor_metrics(self):

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()

        left, top, right, bottom, dpi, handle = 0, 0, sw, sh, 96, None

        if sys.platform.startswith("win"):

            try:

                hwnd = self.root.winfo_id()

                handle = ctypes.windll.user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)

 

                class RECT(ctypes.Structure):

                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),

                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

 

                class MI(ctypes.Structure):

                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),

                                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

 

                info = MI()

                info.cbSize = ctypes.sizeof(MI)

                if ctypes.windll.user32.GetMonitorInfoW(ctypes.c_void_p(handle), ctypes.byref(info)):

                    left, top = info.rcWork.left, info.rcWork.top

                    right, bottom = info.rcWork.right, info.rcWork.bottom

                try:

                    dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96

                except Exception:

                    dpi = ctypes.windll.user32.GetDpiForSystem() or 96

            except Exception:

                pass

        return handle, left, top, right, bottom, dpi

 

    def apply_monitor_layout(self, initial=False):

        self.root.update_idletasks()

        handle, left, top, right, bottom, dpi = self.monitor_metrics()

        width, height = max(760, right-left-8), max(620, bottom-top-8)

        scale = max(1.0, min(2.5, dpi/96.0))

        self.root.tk.call("tk", "scaling", dpi/72.0)

        self.root.geometry(f"{width}x{height}+{left+4}+{top+4}")

        self.root.minsize(min(760, width), min(620, height))

        size = max(9, round(8.5*scale))

        if not hasattr(self, "normal_font"):

            self.normal_font = tkfont.Font(family="Segoe UI", size=size)

            self.bold_font = tkfont.Font(family="Segoe UI", size=size, weight="bold")

            self.title_font = tkfont.Font(family="Segoe UI", size=size+6, weight="bold")

            self.developer_font = tkfont.Font(

                family="Segoe UI",

                size=max(8, size-1),

                slant="italic",

            )

            self.mono_font = tkfont.Font(family="Consolas", size=size)

        else:

            self.normal_font.configure(size=size)

            self.bold_font.configure(size=size)

            self.title_font.configure(size=size+6)

            self.developer_font.configure(size=max(8, size-1))

            self.mono_font.configure(size=size)

        self.monitor_handle = handle

        if not initial and hasattr(self, "log_queue"):

            self.log(f"Monitor changed: {right-left} x {bottom-top}; DPI {dpi}")

 

    def poll_monitor(self):

        try:

            handle, *_ = self.monitor_metrics()

            if handle and handle != self.monitor_handle:

                self.apply_monitor_layout()

        finally:

            self.monitor_job = self.root.after(600, self.poll_monitor)

 

    # ---------------- UI ----------------

    def build_ui(self):

        shell = ttk.Frame(self.root)

        shell.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(shell, highlightthickness=0)

        page_bar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=self.canvas.yview)

        self.canvas.configure(yscrollcommand=page_bar.set)

        page_bar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        outer = ttk.Frame(self.canvas, padding=6)

        window = self.canvas.create_window((0, 0), window=outer, anchor="nw")

        outer.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))

        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta/120), "units"))

 

        header = ttk.Frame(outer)

        header.pack(fill=tk.X)

        ttk.Label(

            header,

            text=APP_TITLE,

            font=self.title_font,

        ).pack(side=tk.LEFT, anchor=tk.W)

        ttk.Label(

            header,

            text="Developed by Anoj Shrestha",

            font=self.developer_font,

            foreground="#555555",

        ).pack(side=tk.RIGHT, anchor=tk.E, padx=(12, 4), pady=(4, 0))

        ttk.Label(

            outer,

            text="Downloads each bundle with curl, shows live progress, then splits files larger than 700 MB and uploads with WinSCP.",

        ).pack(anchor=tk.W, pady=(0,4))

 

        target = ttk.LabelFrame(outer, text="Case and Target", padding=5)

        target.pack(fill=tk.X, pady=2)

        ttk.Label(target, text="Case Number:").grid(row=0, column=0, sticky=tk.W)

        ttk.Entry(target, textvariable=self.case_number, width=28).grid(row=0, column=1, sticky=tk.W, padx=5)

        ttk.Label(target, text="Tier:").grid(row=0, column=2, padx=(20,4))

        for index, value in enumerate(TIERS):

            ttk.Radiobutton(target, text=value, variable=self.tier, value=value,

                            command=self.update_selected_target).grid(row=0, column=3+index, padx=(0,8))

        ttk.Label(target, text="Environment:").grid(row=1, column=0, sticky=tk.W)

        env_frame = ttk.Frame(target)

        env_frame.grid(row=1, column=1, columnspan=5, sticky=tk.W)

        for value in ENVIRONMENTS:

            ttk.Radiobutton(env_frame, text=value, variable=self.environment, value=value,

                            command=self.update_selected_target).pack(side=tk.LEFT, padx=(0,12))

        self.target_label = ttk.Label(target, text="Selected target: Tier 0 / Dev",

                                      foreground="#0067b8", font=self.bold_font)

        self.target_label.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(3,0))

 

        options = ttk.LabelFrame(outer, text="Bundle Options", padding=5)

        options.pack(fill=tk.X, pady=2)

        ttk.Label(options, text="Days of logs:").grid(row=0, column=0, sticky=tk.W)

        self.days_entry = ttk.Entry(options, textvariable=self.days, width=8)

        self.days_entry.grid(row=0, column=1, sticky=tk.W, padx=4)

        ttk.Checkbutton(options, text="Limit included log files", variable=self.limit_logs,

                        command=self.toggle_days).grid(row=0, column=2, sticky=tk.W, padx=12)

        ttk.Label(options, text="When log limiting is cleared, logRetentionDays is omitted from the API request.").grid(row=1, column=0, columnspan=2, sticky=tk.W)

        ttk.Checkbutton(options, text="Include Event Logs", variable=self.include_events).grid(row=1, column=2, sticky=tk.W, padx=12)

 

        urls = ttk.LabelFrame(outer, text="Safeguard Node URLs (one HTTPS base URL per line)", padding=5)

        urls.pack(fill=tk.X, pady=2)

        self.url_container = ttk.Frame(urls)

        self.url_container.pack(fill=tk.X)

        for tier in TIERS:

            for environment in ENVIRONMENTS:

                frame = ttk.Frame(self.url_container)

                ttk.Label(frame, text=f"{tier} / {environment} URLs:", width=20).pack(side=tk.LEFT)

                widget = tk.Text(frame, height=1, width=80, wrap="none", font=self.normal_font)

                widget.pack(side=tk.LEFT, fill=tk.X, expand=True)

                self.url_widgets[(tier, environment)] = (frame, widget)

 

        auth = ttk.LabelFrame(outer, text="Curl and Authentication", padding=5)

        auth.pack(fill=tk.X, pady=2)

        ttk.Label(auth, text="API Path:").grid(row=0, column=0, sticky=tk.W)

        ttk.Entry(auth, textvariable=self.api_path, width=72).grid(row=0, column=1, sticky=tk.W)

        ttk.Label(auth, text="Bearer Token:").grid(row=1, column=0, sticky=tk.W)

        self.token_entry = ttk.Entry(auth, width=72, show="*")

        self.token_entry.grid(row=1, column=1, sticky=tk.W)

 

        output = ttk.LabelFrame(outer, text="Output and WinZip", padding=5)

        output.pack(fill=tk.X, pady=2)

        ttk.Label(output, text="Output Folder:").grid(row=0, column=0, sticky=tk.W)

        ttk.Entry(output, textvariable=self.output_root, width=62).grid(row=0, column=1, sticky=tk.W)

        ttk.Button(output, text="Browse", command=self.pick_output).grid(row=0, column=2)

        ttk.Label(output, text="WinZip command line (WZZIP.EXE):").grid(row=1, column=0, sticky=tk.W)

        ttk.Entry(output, textvariable=self.wzzip_path, width=62).grid(row=1, column=1, sticky=tk.W)

        ttk.Button(output, text="Browse", command=self.pick_wzzip).grid(row=1, column=2)

        ttk.Checkbutton(output, text="Delete original ZIP file after successful split",

                        variable=self.delete_original).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(3,0))

        ttk.Label(output, text="The original oversized ZIP is retained by default and is never uploaded when split parts exist.").grid(row=3, column=0, columnspan=3, sticky=tk.W)

 

        sftp = ttk.LabelFrame(outer, text="WinSCP SFTP Upload", padding=5)

        sftp.pack(fill=tk.X, pady=2)

        ttk.Checkbutton(sftp, text="Automatically upload the complete case-number folder after download/splitting",

                        variable=self.auto_upload).grid(row=0, column=0, columnspan=4, sticky=tk.W)

        ttk.Label(sftp, text="File Protocol:").grid(row=1, column=0, sticky=tk.W)

        ttk.Label(sftp, text="SFTP", foreground="#0067b8", font=self.bold_font).grid(row=1, column=1, sticky=tk.W)

        ttk.Label(sftp, text="Host name:").grid(row=1, column=2, sticky=tk.W, padx=(25,4))

        ttk.Entry(sftp, textvariable=self.sftp_host, width=28).grid(row=1, column=3, sticky=tk.W)

        ttk.Label(sftp, text="Port number:").grid(row=2, column=0, sticky=tk.W)

        ttk.Entry(sftp, textvariable=self.sftp_port, width=10).grid(row=2, column=1, sticky=tk.W)

        ttk.Label(sftp, text="User name:").grid(row=2, column=2, sticky=tk.W, padx=(25,4))

        ttk.Entry(sftp, textvariable=self.sftp_user, width=28).grid(row=2, column=3, sticky=tk.W)

        ttk.Label(sftp, text="Password from Safeguard:").grid(row=3, column=0, sticky=tk.W)

        self.sftp_password_entry = ttk.Entry(sftp, width=38, show="*")

        self.sftp_password_entry.grid(row=3, column=1, sticky=tk.W)

        ttk.Label(sftp, text="Remote root:").grid(row=4, column=0, sticky=tk.W)

        ttk.Entry(sftp, textvariable=self.sftp_root, width=38).grid(row=4, column=1, sticky=tk.W)

        ttk.Label(sftp, text="WinSCP.com:").grid(row=5, column=0, sticky=tk.W)

        ttk.Entry(sftp, textvariable=self.winscp_path, width=58).grid(row=5, column=1, columnspan=2, sticky=tk.W)

        ttk.Button(sftp, text="Browse", command=self.pick_winscp).grid(row=5, column=3, sticky=tk.W)

        ttk.Label(sftp, text="Password is used only in memory. WinSCP accepts and caches a new server key on the first approved connection.").grid(row=6, column=0, columnspan=4, sticky=tk.W)

 

        controls = ttk.Frame(outer)

        controls.pack(fill=tk.X, pady=3)

        self.start_button = ttk.Button(controls, text="Download Support Bundles", command=self.start)

        self.start_button.pack(side=tk.LEFT, padx=2)

        self.stop_button = ttk.Button(controls, text="Stop", command=self.request_stop, state=tk.DISABLED)

        self.stop_button.pack(side=tk.LEFT, padx=2)

        ttk.Button(controls, text="Force Stop Previous/Current Download", command=self.force_stop).pack(side=tk.LEFT, padx=2)

        ttk.Button(controls, text="Exit", command=self.close).pack(side=tk.LEFT, padx=2)

        self.status_label = ttk.Label(controls, text="Ready", font=self.bold_font)

        self.status_label.pack(side=tk.RIGHT)

 

        progress_frame = ttk.Frame(outer)

        progress_frame.pack(fill=tk.X)

        self.progress_bar = ttk.Progressbar(progress_frame, maximum=100)

        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.progress_label = ttk.Label(progress_frame, text="0%", width=7, anchor=tk.E, foreground="#008000")

        self.progress_label.pack(side=tk.RIGHT)

 

        log_frame = ttk.LabelFrame(outer, text="Execution Log", padding=5)

        log_frame.pack(fill=tk.BOTH, expand=True)

        log_controls = ttk.Frame(log_frame)

        log_controls.pack(fill=tk.X)

        ttk.Label(log_controls, text="Log height:").pack(side=tk.LEFT)

        ttk.Button(log_controls, text="-", width=3, command=lambda: self.set_log_height(8)).pack(side=tk.LEFT)

        ttk.Button(log_controls, text="+", width=3, command=lambda: self.set_log_height(16)).pack(side=tk.LEFT)

        ttk.Button(log_controls, text="Expand", command=lambda: self.set_log_height(24)).pack(side=tk.LEFT, padx=(10,2))

        ttk.Button(log_controls, text="Compact", command=lambda: self.set_log_height(8)).pack(side=tk.LEFT, padx=2)

        self.log_line_label = ttk.Label(log_controls, text="0 lines")

        self.log_line_label.pack(side=tk.RIGHT)

        self.log_text = tk.Text(log_frame, height=12, state=tk.DISABLED, wrap="word", font=self.mono_font)

        log_bar = ttk.Scrollbar(log_frame, command=self.log_text.yview)

        self.log_text.configure(yscrollcommand=log_bar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        log_bar.pack(side=tk.RIGHT, fill=tk.Y)

 

    def set_log_height(self, lines):

        self.log_text.configure(height=max(6, min(24, lines)))

 

    def update_selected_target(self):

        for frame, _widget in self.url_widgets.values():

            frame.pack_forget()

        selected = (self.tier.get(), self.environment.get())

        self.url_widgets[selected][0].pack(fill=tk.X)

        if hasattr(self, "target_label"):

            self.target_label.configure(text=f"Selected target: {selected[0]} / {selected[1]}")

 

    def toggle_days(self):

        self.days_entry.configure(state=tk.NORMAL if self.limit_logs.get() else tk.DISABLED)

 

    def pick_output(self):

        selected = filedialog.askdirectory()

        if selected:

            self.output_root.set(selected)

 

    def pick_wzzip(self):

        selected = filedialog.askopenfilename(

            title="Select WZZIP.EXE",

            filetypes=[("WinZip Command Line", "WZZIP.EXE"), ("Executable", "*.exe")],

        )

        if selected:

            self.wzzip_path.set(selected)

 

    def pick_winscp(self):

        selected = filedialog.askopenfilename(

            title="Select WinSCP.com",

            filetypes=[("WinSCP Console", "WinSCP.com"), ("All files", "*.*")],

        )

        if selected:

            self.winscp_path.set(selected)

 

    def selected_urls(self):

        _frame, widget = self.url_widgets[(self.tier.get(), self.environment.get())]

        text = widget.get("1.0", tk.END)

        urls = []

        for raw in re.split(r"[\r\n,]+", text):

            value = raw.strip()

            if not value:

                continue

            html = re.search(r'href=["\']([^"\']+)["\']', value, re.IGNORECASE)

            if html:

                value = html.group(1)

            value = re.sub(r"[\x00-\x20\x7f]", "", value).strip('"\'<>')

            parsed = urlparse(value)

            if parsed.scheme and parsed.netloc:

                value = f"{parsed.scheme}://{parsed.netloc}"

            urls.append(value.rstrip("/"))

        return list(dict.fromkeys(urls))

    # ---------------- Input validation ----------------

    def validate(self):

        case = self.safe_name(self.case_number.get().strip())

        if not case:

            raise AppError("Enter a valid case number.")

        urls = self.selected_urls()

        if not urls:

            raise AppError("Enter at least one Safeguard HTTPS URL.")

        for url in urls:

            parsed = urlparse(url)

            if parsed.scheme.lower() != "https" or not parsed.netloc:

                raise AppError(f"Invalid HTTPS URL: {url}")

        days_text = self.days.get().strip()

        if self.limit_logs.get() and (not days_text.isdigit() or int(days_text) <= 0):

            raise AppError("Days must be a positive whole number.")

        output_text = self.output_root.get().strip()

        if not output_text:

            raise AppError("Select an output folder.")

        output = Path(output_text).expanduser().resolve()

        output.mkdir(parents=True, exist_ok=True)

        token = self.token_entry.get().strip()

        if not token:

            raise AppError("Bearer token is required.")

        curl = shutil.which("curl.exe") or r"C:\Windows\System32\curl.exe"

        if not Path(curl).is_file():

            raise AppError("curl.exe was not found.")

        api = self.api_path.get().strip()

        if not api.startswith("/"):

            raise AppError("API path must start with '/'.")

 

        values = {

            "case": case,

            "urls": urls,

            "limit": self.limit_logs.get(),

            "days": int(days_text) if self.limit_logs.get() else None,

            "events": self.include_events.get(),

            "output": output,

            "token": token,

            "curl": curl,

            "api": api,

        }

        if self.auto_upload.get():

            winscp = Path(self.winscp_path.get().strip())

            if not winscp.is_file() or winscp.name.lower() != "winscp.com":

                raise AppError("Select a valid WinSCP.com.")

            password = self.sftp_password_entry.get()

            if not password:

                raise AppError("Paste the current SFTP password from Safeguard.")

            port = self.sftp_port.get().strip()

            if not port.isdigit() or not 1 <= int(port) <= 65535:

                raise AppError("SFTP port must be between 1 and 65535.")

            values.update({

                "winscp": str(winscp),

                "sftp_password": password,

                "sftp_host": self.sftp_host.get().strip(),

                "sftp_port": int(port),

                "sftp_user": self.sftp_user.get().strip(),

                "sftp_root": "/" + self.sftp_root.get().strip("/"),

            })

        return values

 

    # ---------------- Folder creation ----------------

    @staticmethod

    def resolve_output_root(output_path, tier_name, environment_name, case_name):

        root = Path(output_path).resolve()

        if root.name.casefold() == case_name.casefold():

            env = root.parent

            if env.name.casefold() == environment_name.casefold():

                tier = env.parent

                if tier.name.casefold() == tier_name.casefold():

                    return tier.parent

        if root.name.casefold() == environment_name.casefold() and root.parent.name.casefold() == tier_name.casefold():

            return root.parent.parent

        if root.name.casefold() == tier_name.casefold():

            return root.parent

        return root

 

    @staticmethod

    def create_unique_case_folder(environment_folder, case_name):

        original = environment_folder / case_name

        if not original.exists():

            original.mkdir(parents=True, exist_ok=False)

            return original

        while True:

            now = dt.datetime.now()

            stamp = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"

            candidate = environment_folder / f"{case_name}_{stamp}"

            try:

                candidate.mkdir(parents=True, exist_ok=False)

                return candidate

            except FileExistsError:

                continue

 

    # ---------------- Workflow ----------------

    def start(self):

        if self.worker and self.worker.is_alive():

            return

        old_pid = self.read_lock_pid()

        if old_pid and self.process_exists(old_pid):

            messagebox.showwarning("Collection Running", f"Another collection is active. PID: {old_pid}")

            return

        LOCK_FILE.unlink(missing_ok=True)

        try:

            values = self.validate()

        except Exception as exc:

            messagebox.showerror("Validation Error", str(exc))

            return

        self.stop_event.clear()

        self.set_running(True)

        self.worker = threading.Thread(target=self.run_workflow, args=(values,), daemon=True)

        self.worker.start()

 

    def run_workflow(self, values):

        locked = False

        try:

            self.acquire_lock()

            locked = True

            tier_name = self.safe_name(self.tier.get())

            environment_name = self.safe_name(self.environment.get())

            output_root = self.resolve_output_root(values["output"], tier_name, environment_name, values["case"])

            tier_folder = output_root / tier_name

            environment_folder = tier_folder / environment_name

            environment_folder.mkdir(parents=True, exist_ok=True)

            case_folder = self.create_unique_case_folder(environment_folder, values["case"])

            if case_folder.name == values["case"]:

                self.log(f"Original case folder created: {case_folder.name}")

            else:

                self.log(f"Case already existed. Timestamped case folder created: {case_folder.name}")

            self.log(f"Folder hierarchy: {tier_folder} -> {environment_folder} -> {case_folder}")

 

            results = []

            total_nodes = len(values["urls"])

            self.log(f"Strict sequential mode: {total_nodes} node(s) will be fully downloaded and split before the next node starts.")

            for node_index, node_url in enumerate(values["urls"], start=1):

                if self.stop_event.is_set():

                    raise AppError("Collection stopped by user.")

                self.log(f"Node {node_index} of {total_nodes}: starting download from {node_url}")

                now = dt.datetime.now()

                stamp = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"

                node = self.safe_name(urlparse(node_url).hostname or "node")

                destination = case_folder / f"{case_folder.name}_{node}_{stamp}.zip"

 

                # This call blocks until curl has fully completed and the .partial file

                # has been validated and renamed to the final ZIP.

                self.download(values, node_url, destination)

                if not destination.is_file() or destination.stat().st_size == 0:

                    raise AppError(f"Node {node_index}: downloaded ZIP is missing or empty.")

 

                size_mb = destination.stat().st_size / 1024 / 1024

                self.log(f"Node {node_index} of {total_nodes}: download complete ({size_mb:.2f} MB). Starting 700 MB split check now.")

                self.set_status(f"Node {node_index}/{total_nodes}: checking/splitting")

 

                # This call is also blocking. The next URL cannot be read until split

                # creation and size validation have completed.

                parts = self.split_bundle(destination)

                was_split = parts != [destination]

                if destination.stat().st_size > PART_SIZE_BYTES and not was_split:

                    raise AppError(f"Node {node_index}: file exceeds 700 MB but split parts were not created.")

                if was_split:

                    if not parts or any(not part.is_file() for part in parts):

                        raise AppError(f"Node {node_index}: split validation failed; one or more parts are missing.")

                    self.log(f"Node {node_index} of {total_nodes}: split completed and validated ({len(parts)} part(s)).")

                    if self.delete_original.get():

                        destination.unlink(missing_ok=True)

                        self.log(f"Original ZIP deleted after successful split: {destination.name}")

                    else:

                        self.log(f"Original ZIP retained locally: {destination.name}")

                else:

                    self.log(f"Node {node_index} of {total_nodes}: ZIP is 700 MB or smaller; no split is required.")

 

                results.append({

                    "node": node_url,

                    "original": str(destination),

                    "was_split": was_split,

                    "split_files": [str(part) for part in parts],

                })

                self.log(f"Node {node_index} of {total_nodes}: fully processed. Moving to the next URL only now.")

 

            self.log("All node downloads and split operations completed. SFTP upload can now begin.")

            summary = case_folder / f"{case_folder.name}_summary.json"

            summary.write_text(json.dumps({

                "requested_case_number": values["case"],

                "case_folder": case_folder.name,

                "tier": self.tier.get(),

                "environment": self.environment.get(),

                "delete_original_zip_after_split": self.delete_original.get(),

                "limit_included_log_files": values["limit"],

                "log_retention_days": values["days"],

                "include_event_logs": values["events"],

                "split_size_mb": PART_SIZE_MB,

                "results": results,

                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),

            }, indent=2), encoding="utf-8")

 

            if self.auto_upload.get():

                self.upload(case_folder, values)

            self.set_status("Completed")

            self.root.after(0, lambda: messagebox.showinfo("Completed", f"Workflow completed.\n\nCase folder:\n{case_folder}"))

        except Exception as exc:

            self.log(str(exc), "ERROR")

            self.set_status("Failed")

            self.root.after(0, lambda error=str(exc): messagebox.showerror("Workflow Failed", error))

        finally:

            self.current_process = None

            if locked:

                LOCK_FILE.unlink(missing_ok=True)

            self.set_running(False)

 

    # ---------------- Curl download ----------------

    def download(self, values, node_url, destination):

        partial = destination.with_suffix(".zip.partial")

        header_file = destination.with_suffix(".curl-headers.txt")

        partial.unlink(missing_ok=True)

        header_file.unlink(missing_ok=True)

 

        parsed_node = urlparse(node_url.strip())

        if parsed_node.scheme.lower() != "https" or not parsed_node.netloc:

            raise AppError(f"Invalid node base URL: {node_url!r}")

 

        clean_node_url = f"https://{parsed_node.netloc}"

        api_path = "/" + values["api"].strip().lstrip("/")

        query = {

            "includeEventLogs": "true" if values["events"] else "false"

        }

        if values["limit"]:

            query["logRetentionDays"] = str(values["days"])

 

        request_url = (

            clean_node_url

            + quote(api_path, safe="/%")

            + "?"

            + urlencode(query)

        )

 

        # --fail-with-body returns curl exit code 22 for an HTTP 4xx/5xx,

        # while preserving the server response body in the partial file.

        # --write-out gives us the real HTTP status and effective URL even

        # when curl's last stderr line does not end with a newline.

        command = [

            values["curl"],

            "-k",

            "--http1.1",

            "--noproxy",

            "*",

            "--fail-with-body",

            "--location",

            "--progress-bar",

            "--show-error",

            "--request",

            "GET",

            "--header",

            f"Authorization: Bearer {values['token']}",

            "--dump-header",

            str(header_file),

            "--write-out",

            "\n__HTTP_CODE__=%{http_code}\n__EFFECTIVE_URL__=%{url_effective}\n",

            "--url",

            request_url,

            "--output",

            str(partial),

        ]

 

        self.log(f"Downloading {destination.name} from {clean_node_url}")

        self.log(f"Validated request URL: {request_url}")

        self.set_progress(0)

        self.set_status("Generating bundle / waiting for download")

 

        process = subprocess.Popen(

            command,

            stdout=subprocess.PIPE,

            stderr=subprocess.PIPE,

            text=True,

            shell=False,

            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),

            bufsize=0,

        )

        self.current_process = process

        buffer = ""

        stderr_lines = []

 

        while True:

            char = process.stderr.read(1)

            if char == "" and process.poll() is not None:

                break

 

            if char in ("\r", "\n"):

                line = buffer.strip()

                buffer = ""

                if line:

                    matches = re.findall(r"(\d{1,3}(?:\.\d+)?)%", line)

                    if matches:

                        percent = min(100.0, float(matches[-1]))

                        self.set_progress(percent)

                        self.set_status(f"Downloading: {percent:.0f}%")

                    else:

                        stderr_lines.append(line)

            elif char:

                buffer += char

 

            if self.stop_event.is_set() and process.poll() is None:

                self.kill_tree(process.pid)

                partial.unlink(missing_ok=True)

                header_file.unlink(missing_ok=True)

                self.current_process = None

                raise AppError("Download stopped by user.")

 

        # Preserve an unterminated final stderr line. The old version dropped

        # this buffer, which caused 'No error text returned.' after packaging.

        if buffer.strip():

            stderr_lines.append(buffer.strip())

 

        stdout_text = process.stdout.read() if process.stdout else ""

        code = process.wait()

        self.current_process = None

 

        http_match = re.search(r"__HTTP_CODE__=(\d{3})", stdout_text)

        http_code = http_match.group(1) if http_match else "unknown"

 

        effective_match = re.search(r"__EFFECTIVE_URL__=(.+)", stdout_text)

        effective_url = (

            effective_match.group(1).strip()

            if effective_match

            else request_url

        )

 

        header_statuses = []

        if header_file.is_file():

            try:

                for line in header_file.read_text(

                    encoding="utf-8",

                    errors="replace",

                ).splitlines():

                    if line.upper().startswith("HTTP/"):

                        header_statuses.append(line.strip())

            except OSError:

                pass

 

        if code != 0:

            response_preview = ""

            if partial.is_file() and partial.stat().st_size:

                try:

                    raw_preview = partial.read_bytes()[:4096]

                    response_preview = raw_preview.decode(

                        "utf-8",

                        errors="replace",

                    ).strip()

                    response_preview = re.sub(r"\s+", " ", response_preview)

                    response_preview = response_preview[:1000]

                except OSError:

                    response_preview = ""

 

            diagnostic_parts = [

                f"curl exit code={code}",

                f"HTTP status={http_code}",

                f"effective URL={effective_url}",

            ]

            if header_statuses:

                diagnostic_parts.append(

                    "HTTP response=" + " -> ".join(header_statuses)

                )

            if stderr_lines:

                diagnostic_parts.append(

                    "curl message=" + " | ".join(stderr_lines[-10:])

                )

            if response_preview:

                diagnostic_parts.append(

                    "server response=" + response_preview

                )

 

            partial.unlink(missing_ok=True)

            header_file.unlink(missing_ok=True)

 

            raise AppError(

                "Support bundle request failed: "

                + "; ".join(diagnostic_parts)

            )

 

        header_file.unlink(missing_ok=True)

 

        if not partial.exists() or partial.stat().st_size == 0:

            partial.unlink(missing_ok=True)

            raise AppError(

                "curl completed without creating a non-empty support bundle. "

                f"HTTP status={http_code}; effective URL={effective_url}"

            )

 

        with partial.open("rb") as handle:

            start = handle.read(512).lstrip().lower()

 

        if start.startswith((b"<html", b"<!doctype html", b"{")):

            preview = start.decode("utf-8", errors="replace")[:300]

            partial.unlink(missing_ok=True)

            raise AppError(

                "Safeguard returned HTML/JSON instead of ZIP: "

                f"HTTP status={http_code}; response={preview}"

            )

 

        partial.replace(destination)

        self.set_progress(100)

        self.log(

            f"Downloaded: {destination.name} "

            f"({destination.stat().st_size/1024/1024:.2f} MB); "

            f"HTTP status={http_code}"

        )

 

    # ---------------- Validated 700 MiB splitter ----------------

    def split_bundle(self, source):

        total = source.stat().st_size

        self.log(f"Split check started for {source.name}: {total/1024/1024:.2f} MB; maximum part size is {PART_SIZE_MB} MB.")

        if total <= PART_SIZE_BYTES:

            self.log("No split required; file is 700 MB or smaller.")

            return [source]

 

        wzzip = Path(self.wzzip_path.get().strip())

        if wzzip.is_file():

            self.log(f"WZZIP detected: {wzzip}")

        else:

            self.log("WZZIP.EXE not found; using validated built-in splitter.", "WARNING")

        # No undocumented WZZIP switches are invoked. This prevents the prior

        # WinZip parameter-validation dialog while still detecting WZZIP.

        prefix = source.with_name(source.stem + "_split")

        for old in source.parent.glob(prefix.name + ".part*"):

            old.unlink(missing_ok=True)

        self.set_progress(0)

        self.set_status("Splitting: 0%")

        parts, processed, number = [], 0, 1

        try:

            with source.open("rb") as source_handle:

                while processed < total:

                    if self.stop_event.is_set():

                        raise AppError("Splitting stopped by user.")

                    part = source.parent / f"{prefix.name}.part{number:03d}"

                    written = 0

                    with part.open("wb") as output_handle:

                        while written < PART_SIZE_BYTES:

                            if self.stop_event.is_set():

                                raise AppError("Splitting stopped by user.")

                            data = source_handle.read(min(8*1024*1024, PART_SIZE_BYTES-written))

                            if not data:

                                break

                            output_handle.write(data)

                            written += len(data)

                            processed += len(data)

                            percent = min(99, int(processed*100/total))

                            self.set_progress(percent)

                            self.set_status(f"Splitting: {percent}%")

                    if part.stat().st_size == 0:

                        part.unlink(missing_ok=True)

                        break

                    parts.append(part)

                    self.log(f"Created split part: {part.name} ({part.stat().st_size/1024/1024:.2f} MB)")

                    number += 1

        except Exception:

            for part in source.parent.glob(prefix.name + ".part*"):

                part.unlink(missing_ok=True)

            raise

        if not parts or sum(part.stat().st_size for part in parts) != total:

            for part in parts:

                part.unlink(missing_ok=True)

            raise AppError("Split validation failed: combined part size does not equal source size.")

        if any(part.stat().st_size > PART_SIZE_BYTES for part in parts):

            for part in parts:

                part.unlink(missing_ok=True)

            raise AppError("Split validation failed: a part exceeds 700 MB.")

        self.set_progress(100)

        self.set_status("Split complete: 100%")

        self.log(f"Split validation passed: {len(parts)} part(s).")

        return parts

 

    # ---------------- WinSCP upload ----------------

    def upload(self, folder, values):

        upload_files = []

        for item in folder.iterdir():

            if not item.is_file():

                continue

            if item.suffix.lower() == ".zip" and item.stat().st_size > PART_SIZE_BYTES:

                self.log(f"Skipping retained oversized original ZIP during upload: {item.name}")

                continue

            if item.stat().st_size > PART_SIZE_BYTES:

                raise AppError(f"Upload blocked; file exceeds 700 MB: {item.name}")

            upload_files.append(item)

        if not upload_files:

            raise AppError("No validated files are available for SFTP upload.")

 

        remote = values["sftp_root"].rstrip("/") + "/" + folder.name

        user = quote(values["sftp_user"], safe="")

        password = quote(values["sftp_password"], safe="")

        session_url = f"sftp://{user}:{password}@{values['sftp_host']}:{values['sftp_port']}/"

        commands = [

            "option batch abort", "option confirm off",

            f'open "{session_url}" -hostkey="acceptnew"',

            f'mkdir "{remote}"', f'cd "{remote}"', f'lcd "{folder}"',

        ]

        commands.extend(f'put -resume "{item.name.replace(chr(34), chr(34)*2)}"' for item in upload_files)

        commands.append("exit")

        script = "\n".join(commands) + "\n"

        self.log(f"Starting WinSCP upload to {remote}")

        self.set_progress(0)

        self.set_status("Uploading: 0%")

        process = subprocess.Popen(

            [values["winscp"], "/ini=nul", "/nointeractiveinput", "/stdout"],

            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,

            text=True, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),

        )

        self.current_process = process

        process.stdin.write(script)

        process.stdin.close()

        lines = []

        for line in process.stdout:

            clean = line.strip()

            if clean:

                lines.append(clean)

                matches = re.findall(r"(\d{1,3}(?:\.\d+)?)%", clean)

                if matches:

                    percent = min(100.0, float(matches[-1]))

                    self.set_progress(percent)

                    self.set_status(f"Uploading: {percent:.0f}%")

            if self.stop_event.is_set() and process.poll() is None:

                self.kill_tree(process.pid)

                self.current_process = None

                raise AppError("Upload stopped by user.")

        code = process.wait()

        self.current_process = None

        if code != 0:

            error = " | ".join(lines[-20:]).replace(values["sftp_password"], "<redacted>")

            raise AppError(f"WinSCP upload failed: {error or 'No error text returned.'}")

        self.set_progress(100)

        self.set_status("Upload complete: 100%")

        self.log(f"WinSCP upload completed to {remote}")

 

    # ---------------- Stop, lock, logging ----------------

    def request_stop(self):

        self.stop_event.set()

        self.set_status("Stop requested")

 

    def force_stop(self):

        pid = self.current_process.pid if self.current_process and self.current_process.poll() is None else self.read_lock_pid()

        if pid and messagebox.askyesno("Force Stop", f"Terminate process tree PID {pid}?"):

            self.kill_tree(pid)

        LOCK_FILE.unlink(missing_ok=True)

        self.current_process = None

        self.set_status("Force stopped")

        self.set_progress(0)

 

    def acquire_lock(self):

        try:

            descriptor = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:

                handle.write(f"pid={os.getpid()}\n")

        except FileExistsError as exc:

            raise AppError(f"Another collection may be running. Lock file: {LOCK_FILE}") from exc

 

    def detect_previous(self):

        pid = self.read_lock_pid()

        if pid and self.process_exists(pid):

            self.log(f"Previous collection detected: PID {pid}", "WARNING")

 

    @staticmethod

    def read_lock_pid():

        try:

            for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():

                if line.startswith("pid="):

                    return int(line.split("=",1)[1])

        except Exception:

            pass

        return None

 

    @staticmethod

    def process_exists(pid):

        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,

                                text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        return str(pid) in result.stdout

 

    @staticmethod

    def kill_tree(pid):

        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,

                       text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

 

    def set_running(self, running):

        self.root.after(0, lambda: (

            self.start_button.configure(state=tk.DISABLED if running else tk.NORMAL),

            self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED),

        ))

 

    def set_progress(self, value):

        value = max(0.0, min(100.0, float(value)))

        self.root.after(0, lambda: (

            self.progress_bar.configure(value=value),

            self.progress_label.configure(text=f"{value:.0f}%"),

        ))

 

    def set_status(self, text):

        self.root.after(0, lambda: self.status_label.configure(text=text))

 

    def log(self, message, level="INFO"):

        self.log_queue.put(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} | {level:<7} | {message}")

 

    def poll_logs(self):

        try:

            while True:

                line = self.log_queue.get_nowait()

                self.log_text.configure(state=tk.NORMAL)

                self.log_text.insert(tk.END, line + "\n")

                self.log_text.see(tk.END)

                self.log_text.configure(state=tk.DISABLED)

                count = int(self.log_text.index("end-1c").split(".")[0])

                self.log_line_label.configure(text=f"{count} lines")

        except queue.Empty:

            pass

        self.root.after(200, self.poll_logs)

 

    @staticmethod

    def safe_name(value):

        return INVALID_NAME.sub("_", value).strip().replace(" ", "_")

 

    @staticmethod

    def find_wzzip():

        for candidate in (shutil.which("WZZIP.EXE"), WZZIP_DEFAULT,

                          r"C:\Program Files (x86)\WinZip\WZZIP.EXE"):

            if candidate and Path(candidate).is_file():

                return str(candidate)

        return None

 

    @staticmethod

    def find_winscp():

        for candidate in (shutil.which("WinSCP.com"), r"C:\Program Files (x86)\WinSCP\WinSCP.com",

                          r"C:\Program Files\WinSCP\WinSCP.com"):

            if candidate and Path(candidate).is_file():

                return str(candidate)

        return None

 

    def close(self):

        if self.monitor_job:

            try:

                self.root.after_cancel(self.monitor_job)

            except tk.TclError:

                pass

        if self.worker and self.worker.is_alive() and not messagebox.askyesno("Exit", "An operation is running. Exit anyway?"):

            return

        self.stop_event.set()

        if self.current_process and self.current_process.poll() is None:

            self.kill_tree(self.current_process.pid)

        self.root.destroy()

 

def main():

    enable_dpi_awareness()

    root = tk.Tk()

    app = SupportBundleApp(root)

    root.protocol("WM_DELETE_WINDOW", app.close)

    root.mainloop()

 

if __name__ == "__main__":

    main()