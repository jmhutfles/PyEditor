from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

from .ffmpeg_service import FfmpegNotFoundError, probe_duration
from .models import ClipSegment, RenderJob
from .preview_player import PreviewPlayer
from .proxy_service import find_existing_proxy
from .proxy_worker import ProxyController
from .render_worker import RenderController


class MainWindow:
    def __init__(self) -> None:
        if TkinterDnD is not None:
            self.root = TkinterDnD.Tk()
            self.drag_and_drop_enabled = True
        else:
            self.root = tk.Tk()
            self.drag_and_drop_enabled = False
        self.root.title("PyEditor")
        self.root.geometry("1280x820")

        self.current_clips: list[ClipSegment] = []
        self.queued_outputs: set[Path] = set()
        self.queue_rows: dict[str, int] = {}
        self.render_controller = RenderController()
        self.proxy_controller = ProxyController()
        self.preview_player = PreviewPlayer()
        self.preview_image: ImageTk.PhotoImage | None = None
        self._updating_playhead = False

        self.selected_clip_name_var = tk.StringVar(value="No clip selected")
        self.selected_clip_duration_var = tk.StringVar(value="-")
        self.start_var = tk.StringVar(value="0.000")
        self.end_var = tk.StringVar(value="0.000")
        self.output_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.preview_position_var = tk.DoubleVar(value=0.0)
        self.preview_time_var = tk.StringVar(value="00:00:00.000")
        self.preview_duration_var = tk.StringVar(value="00:00:00.000")
        self.preview_state_var = tk.StringVar(value="No preview loaded")
        self.drop_hint_var = tk.StringVar(
            value="Drag video files here or use Add Clips" if self.drag_and_drop_enabled else "Use Add Clips to load video files"
        )
        self.render_progress_var = tk.DoubleVar(value=0.0)
        self.render_progress_label_var = tk.StringVar(value="Idle")
        self.proxy_status_var = tk.StringVar(value="No preview loaded")
        self._last_progress_message = ""

        self._build_ui()
        self._poll_render_events()
        self._poll_proxy_events()
        self._poll_preview()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
        self.root.rowconfigure(0, weight=1)

        left_frame = ttk.Frame(self.root, padding=16)
        right_frame = ttk.Frame(self.root, padding=16)
        left_frame.grid(row=0, column=0, sticky="nsew")
        right_frame.grid(row=0, column=1, sticky="nsew")

        self._build_edit_panel(left_frame)
        self._build_queue_panel(right_frame)

        status_bar = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(16, 8))
        status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")

    def _build_edit_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Current Edit",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Load clips, trim them with start/end times, then queue the edit for rendering.",
        ).pack(anchor="w", pady=(4, 0))

        preview_box = ttk.LabelFrame(parent, text="Preview", padding=12)
        preview_box.grid(row=1, column=0, sticky="ew")
        preview_box.columnconfigure(0, weight=1)

        self.preview_label = ttk.Label(preview_box, text="Select a clip to preview", anchor="center")
        self.preview_label.grid(row=0, column=0, sticky="ew")

        self.playhead_scale = ttk.Scale(
            preview_box,
            from_=0.0,
            to=0.0,
            variable=self.preview_position_var,
            command=self._on_playhead_changed,
        )
        self.playhead_scale.grid(row=1, column=0, sticky="ew", pady=(12, 0))

        preview_info_row = ttk.Frame(preview_box)
        preview_info_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        preview_info_row.columnconfigure(1, weight=1)
        ttk.Label(preview_info_row, textvariable=self.preview_time_var).grid(row=0, column=0, sticky="w")
        ttk.Label(preview_info_row, textvariable=self.preview_state_var).grid(row=0, column=1)
        ttk.Label(preview_info_row, textvariable=self.preview_duration_var).grid(row=0, column=2, sticky="e")
        ttk.Label(preview_box, textvariable=self.proxy_status_var).grid(row=4, column=0, sticky="w", pady=(8, 0))

        preview_controls = ttk.Frame(preview_box)
        preview_controls.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.play_pause_button = ttk.Button(preview_controls, text="Play", command=self._toggle_preview_playback)
        self.play_pause_button.pack(side="left")
        ttk.Button(preview_controls, text="Mark In", command=self._set_start_from_playhead).pack(side="left", padx=(8, 0))
        ttk.Button(preview_controls, text="Mark Out", command=self._set_end_from_playhead).pack(side="left", padx=(8, 0))
        ttk.Button(preview_controls, text="Jump To In", command=lambda: self._jump_to_trim_edge("start")).pack(side="left", padx=(8, 0))
        ttk.Button(preview_controls, text="Jump To Out", command=lambda: self._jump_to_trim_edge("end")).pack(side="left", padx=(8, 0))

        clips_box = ttk.LabelFrame(parent, text="Clips", padding=12)
        clips_box.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        clips_box.columnconfigure(0, weight=1)
        clips_box.rowconfigure(1, weight=1)

        button_row = ttk.Frame(clips_box)
        button_row.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Button(button_row, text="Add Clips", command=self._add_clips).pack(side="left")
        ttk.Button(button_row, text="Remove", command=self._remove_selected_clip).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Move Up", command=lambda: self._move_selected_clip(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Move Down", command=lambda: self._move_selected_clip(1)).pack(side="left", padx=(8, 0))

        ttk.Label(clips_box, textvariable=self.drop_hint_var).grid(row=1, column=0, sticky="w", pady=(0, 8))

        list_frame = ttk.Frame(clips_box)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.clip_listbox = tk.Listbox(list_frame, exportselection=False, height=16)
        self.clip_listbox.grid(row=0, column=0, sticky="nsew")
        self.clip_listbox.bind("<<ListboxSelect>>", self._on_clip_selected)
        clips_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.clip_listbox.yview)
        clips_scrollbar.grid(row=0, column=1, sticky="ns")
        self.clip_listbox.configure(yscrollcommand=clips_scrollbar.set)
        self._configure_drag_and_drop(clips_box, list_frame)

        trim_box = ttk.LabelFrame(parent, text="Selected Clip", padding=12)
        trim_box.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        trim_box.columnconfigure(1, weight=1)

        ttk.Label(trim_box, text="Clip").grid(row=0, column=0, sticky="w")
        ttk.Label(trim_box, textvariable=self.selected_clip_name_var).grid(row=0, column=1, sticky="w")

        ttk.Label(trim_box, text="Original duration").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(trim_box, textvariable=self.selected_clip_duration_var).grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(trim_box, text="Trim start (seconds)").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(trim_box, textvariable=self.start_var).grid(row=2, column=1, sticky="ew", pady=(8, 0))

        ttk.Label(trim_box, text="Trim end (seconds)").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(trim_box, textvariable=self.end_var).grid(row=3, column=1, sticky="ew", pady=(8, 0))

        trim_button_row = ttk.Frame(trim_box)
        trim_button_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(trim_button_row, text="Apply Trim", command=self._apply_trim_values).pack(side="left")
        ttk.Button(trim_button_row, text="Use Full Clip", command=self._use_full_clip).pack(side="left", padx=(8, 0))

        output_box = ttk.LabelFrame(parent, text="Output", padding=12)
        output_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        output_box.columnconfigure(0, weight=1)
        ttk.Entry(output_box, textvariable=self.output_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_box, text="Browse", command=self._choose_output_path).grid(row=0, column=1, padx=(8, 0))

        action_row = ttk.Frame(parent)
        action_row.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(action_row, text="Queue Current Edit", command=self._queue_current_job).pack(side="left")
        self.start_queue_button = ttk.Button(action_row, text="Start Render Queue", command=self._start_render_queue)
        self.start_queue_button.pack(side="left", padx=(8, 0))

    def _build_queue_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(3, weight=1)

        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header,
            text="Render Queue",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Queued jobs render in the background while you prepare the next one.",
        ).pack(anchor="w", pady=(4, 0))

        queue_box = ttk.LabelFrame(parent, text="Queued Jobs", padding=12)
        queue_box.grid(row=1, column=0, sticky="nsew")
        queue_box.columnconfigure(0, weight=1)
        queue_box.rowconfigure(0, weight=1)

        queue_frame = ttk.Frame(queue_box)
        queue_frame.grid(row=0, column=0, sticky="nsew")
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        self.queue_listbox = tk.Listbox(queue_frame, exportselection=False, height=12)
        self.queue_listbox.grid(row=0, column=0, sticky="nsew")
        queue_scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.queue_listbox.yview)
        queue_scrollbar.grid(row=0, column=1, sticky="ns")
        self.queue_listbox.configure(yscrollcommand=queue_scrollbar.set)

        progress_box = ttk.LabelFrame(parent, text="Current Render", padding=12)
        progress_box.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        progress_box.columnconfigure(0, weight=1)
        ttk.Label(progress_box, textvariable=self.render_progress_label_var).grid(row=0, column=0, sticky="w")
        self.render_progressbar = ttk.Progressbar(
            progress_box,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            variable=self.render_progress_var,
        )
        self.render_progressbar.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        log_box = ttk.LabelFrame(parent, text="Render Log", padding=12)
        log_box.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        self.log_output = tk.Text(log_box, height=16, wrap="word")
        self.log_output.grid(row=0, column=0, sticky="nsew")
        self.log_output.configure(state="disabled")
        log_scrollbar = ttk.Scrollbar(log_box, orient="vertical", command=self.log_output.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_output.configure(yscrollcommand=log_scrollbar.set)

    def _add_clips(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select Clips",
            filetypes=[("Video Files", "*.mp4 *.mov *.mxf *.avi *.mkv"), ("All Files", "*.*")],
        )
        if not paths:
            return

        self._add_clip_paths([Path(path_text) for path_text in paths])

    def _add_clip_paths(self, clip_paths: list[Path]) -> None:
        added_any = False

        for clip_path in clip_paths:
            if not clip_path.exists() or not clip_path.is_file():
                continue
            try:
                duration_seconds = probe_duration(clip_path)
            except FfmpegNotFoundError as exc:
                messagebox.showerror("ffmpeg missing", str(exc), parent=self.root)
                return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("Clip load failed", str(exc), parent=self.root)
                return

            clip = ClipSegment(
                source_path=clip_path,
                start_seconds=0.0,
                end_seconds=duration_seconds,
                duration_seconds=duration_seconds,
            )
            existing_proxy = find_existing_proxy(clip_path)
            if existing_proxy is not None:
                clip.proxy_path = existing_proxy
                clip.proxy_status = "ready"
            else:
                clip.proxy_status = "queued"
                self.proxy_controller.enqueue(str(clip_path))
            self.current_clips.append(clip)
            self.clip_listbox.insert(tk.END, self._format_clip_item_text(clip))
            added_any = True

        if not added_any:
            messagebox.showwarning(
                "No clips added",
                "No valid video files were added. Drop video files or use the Add Clips button.",
                parent=self.root,
            )
            return

        if self.clip_listbox.curselection() == () and self.current_clips:
            self.clip_listbox.selection_set(0)
            self._load_selected_clip(0)

        if not self.output_path_var.get() and self.current_clips:
            suggested_path = self.current_clips[0].source_path.with_name("edit-output.mp4")
            self.output_path_var.set(str(suggested_path))

    def _configure_drag_and_drop(self, *widgets: tk.Misc) -> None:
        if not self.drag_and_drop_enabled or DND_FILES is None:
            return

        for widget in widgets:
            if hasattr(widget, "drop_target_register") and hasattr(widget, "dnd_bind"):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._handle_clip_drop)

    def _handle_clip_drop(self, event: tk.Event[tk.Misc]) -> str:
        dropped_items = [Path(item) for item in self.root.tk.splitlist(event.data)]
        self._add_clip_paths(dropped_items)
        return "break"

    def _remove_selected_clip(self) -> None:
        row = self._selected_clip_index()
        if row is None:
            return

        self.current_clips.pop(row)
        self.clip_listbox.delete(row)
        if self.current_clips:
            next_row = min(row, len(self.current_clips) - 1)
            self.clip_listbox.selection_set(next_row)
            self._load_selected_clip(next_row)
        else:
            self._clear_clip_selection()

    def _move_selected_clip(self, offset: int) -> None:
        row = self._selected_clip_index()
        if row is None:
            return

        new_row = row + offset
        if new_row < 0 or new_row >= len(self.current_clips):
            return

        self.current_clips[row], self.current_clips[new_row] = self.current_clips[new_row], self.current_clips[row]
        self._refresh_clip_list_text()
        self.clip_listbox.selection_clear(0, tk.END)
        self.clip_listbox.selection_set(new_row)
        self._load_selected_clip(new_row)

    def _on_clip_selected(self, _event: tk.Event[tk.Misc]) -> None:
        row = self._selected_clip_index()
        if row is None:
            self._clear_clip_selection()
            return
        self._load_selected_clip(row)

    def _load_selected_clip(self, row: int) -> None:
        clip = self.current_clips[row]
        self.selected_clip_name_var.set(clip.display_name)
        self.selected_clip_duration_var.set(_format_seconds(clip.duration_seconds or 0.0))
        self.start_var.set(f"{clip.start_seconds:.3f}")
        self.end_var.set(f"{(clip.effective_end or 0.0):.3f}")
        self._load_preview_for_clip(clip)

    def _clear_clip_selection(self) -> None:
        self.selected_clip_name_var.set("No clip selected")
        self.selected_clip_duration_var.set("-")
        self.start_var.set("0.000")
        self.end_var.set("0.000")
        self.preview_player.release()
        self._set_preview_image(None)
        self.preview_state_var.set("No preview loaded")
        self.proxy_status_var.set("No preview loaded")
        self.preview_time_var.set("00:00:00.000")
        self.preview_duration_var.set("00:00:00.000")
        self.play_pause_button.configure(text="Play")
        self._set_playhead_value(0.0, 0.0)

    def _apply_trim_values(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            messagebox.showwarning("No clip selected", "Select a clip first.", parent=self.root)
            return

        try:
            start_seconds = float(self.start_var.get())
            end_seconds = float(self.end_var.get())
        except ValueError:
            messagebox.showwarning("Invalid trim", "Start and end must be numbers.", parent=self.root)
            return

        clip_duration = clip.duration_seconds or 0.0
        start_seconds = max(0.0, min(start_seconds, clip_duration))
        end_seconds = max(start_seconds, min(end_seconds, clip_duration))

        clip.start_seconds = start_seconds
        clip.end_seconds = end_seconds
        self.start_var.set(f"{start_seconds:.3f}")
        self.end_var.set(f"{end_seconds:.3f}")
        self._refresh_clip_list_text()

    def _use_full_clip(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            return
        clip.start_seconds = 0.0
        clip.end_seconds = clip.duration_seconds
        self.start_var.set("0.000")
        self.end_var.set(f"{(clip.duration_seconds or 0.0):.3f}")
        self._refresh_clip_list_text()

    def _choose_output_path(self) -> None:
        output_path_text = filedialog.asksaveasfilename(
            title="Choose Output File",
            defaultextension=".mp4",
            filetypes=[("MP4 Files", "*.mp4")],
        )
        if output_path_text:
            self.output_path_var.set(output_path_text)

    def _load_preview_for_clip(self, clip: ClipSegment) -> None:
        try:
            image = self.preview_player.load_clip(clip.preview_path, clip.duration_seconds or 0.0)
        except Exception as exc:  # noqa: BLE001
            self.preview_player.release()
            self._set_preview_image(None)
            self.preview_state_var.set("Preview unavailable")
            messagebox.showerror("Preview failed", str(exc), parent=self.root)
            return

        self.preview_state_var.set("Paused")
        self.proxy_status_var.set(self._format_proxy_status(clip))
        self.preview_duration_var.set(_format_seconds(self.preview_player.duration_seconds))
        self.play_pause_button.configure(text="Play")
        self._set_preview_image(image)
        self._seek_preview(clip.start_seconds)

    def _toggle_preview_playback(self) -> None:
        if self._selected_clip() is None:
            return

        self.preview_player.toggle()
        self.preview_state_var.set("Playing" if self.preview_player.is_playing else "Paused")
        self.play_pause_button.configure(text="Pause" if self.preview_player.is_playing else "Play")

    def _on_playhead_changed(self, raw_value: str) -> None:
        if self._updating_playhead:
            return

        try:
            seconds = float(raw_value)
        except ValueError:
            return

        self.preview_player.pause()
        self.play_pause_button.configure(text="Play")
        self.preview_state_var.set("Scrubbing")
        self._seek_preview(seconds)

    def _seek_preview(self, seconds: float) -> None:
        image = self.preview_player.seek(seconds)
        self._set_preview_image(image)
        self._set_playhead_value(self.preview_player.playhead_seconds, self.preview_player.duration_seconds)

    def _set_preview_image(self, image: Image.Image | None) -> None:
        if image is None:
            self.preview_image = None
            self.preview_label.configure(image="", text="Select a clip to preview")
            return

        self.preview_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self.preview_image, text="")

    def _set_playhead_value(self, position_seconds: float, duration_seconds: float) -> None:
        self._updating_playhead = True
        try:
            self.playhead_scale.configure(to=max(duration_seconds, 0.001))
            self.preview_position_var.set(position_seconds)
        finally:
            self._updating_playhead = False
        self.preview_time_var.set(_format_seconds(position_seconds))
        self.preview_duration_var.set(_format_seconds(duration_seconds))

    def _set_start_from_playhead(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            return

        clip.start_seconds = min(self.preview_player.playhead_seconds, clip.effective_end or self.preview_player.playhead_seconds)
        self.start_var.set(f"{clip.start_seconds:.3f}")
        self._refresh_clip_list_text()

    def _set_end_from_playhead(self) -> None:
        clip = self._selected_clip()
        if clip is None:
            return

        clip.end_seconds = max(self.preview_player.playhead_seconds, clip.start_seconds)
        self.end_var.set(f"{clip.end_seconds:.3f}")
        self._refresh_clip_list_text()

    def _jump_to_trim_edge(self, edge: str) -> None:
        clip = self._selected_clip()
        if clip is None:
            return

        target_seconds = clip.start_seconds if edge == "start" else (clip.effective_end or 0.0)
        self.preview_player.pause()
        self.play_pause_button.configure(text="Play")
        self.preview_state_var.set("Paused")
        self._seek_preview(target_seconds)

    def _queue_current_job(self) -> None:
        output_path_text = self.output_path_var.get().strip()
        if not self.current_clips:
            messagebox.showwarning("Nothing to queue", "Add at least one clip first.", parent=self.root)
            return
        if not output_path_text:
            messagebox.showwarning("Missing output", "Choose an output file first.", parent=self.root)
            return

        output_path = Path(output_path_text)
        if output_path in self.queued_outputs:
            messagebox.showwarning(
                "Duplicate output",
                "That output file is already in the queue. Choose a different name or wait for it to finish.",
                parent=self.root,
            )
            return

        self._apply_trim_values_if_possible()

        copied_clips = [
            ClipSegment(
                source_path=clip.source_path,
                start_seconds=clip.start_seconds,
                end_seconds=clip.effective_end,
                duration_seconds=clip.duration_seconds,
            )
            for clip in self.current_clips
        ]
        job = RenderJob(output_path=output_path, clips=copied_clips)
        self.render_controller.enqueue(job)

        queue_key = str(output_path)
        row = self.queue_listbox.size()
        self.queue_rows[queue_key] = row
        self.queue_listbox.insert(tk.END, f"Queued | {output_path.name}")
        self.queued_outputs.add(output_path)

        self._append_log(f"Queued {output_path}")
        self._clear_current_edit()
        self.status_var.set(f"Queued {output_path.name}")

    def _apply_trim_values_if_possible(self) -> None:
        row = self._selected_clip_index()
        if row is None:
            return
        try:
            self._apply_trim_values()
        except Exception:
            return

    def _clear_current_edit(self) -> None:
        self.current_clips.clear()
        self.clip_listbox.delete(0, tk.END)
        self.output_path_var.set("")
        self._clear_clip_selection()

    def _start_render_queue(self) -> None:
        if not self.render_controller.has_pending_jobs() and not self.render_controller.is_running:
            messagebox.showinfo("Queue empty", "Add at least one job to the render queue first.", parent=self.root)
            return

        self.render_controller.start()
        self.start_queue_button.configure(state="disabled")
        self.status_var.set("Rendering queue")

    def _selected_clip_index(self) -> int | None:
        selection = self.clip_listbox.curselection()
        if not selection:
            return None
        return int(selection[0])

    def _selected_clip(self) -> ClipSegment | None:
        row = self._selected_clip_index()
        if row is None:
            return None
        return self.current_clips[row]

    def _refresh_clip_list_text(self) -> None:
        selected_row = self._selected_clip_index()
        self.clip_listbox.delete(0, tk.END)
        for clip in self.current_clips:
            self.clip_listbox.insert(tk.END, self._format_clip_item_text(clip))
        if selected_row is not None and selected_row < len(self.current_clips):
            self.clip_listbox.selection_set(selected_row)

    def _format_clip_item_text(self, clip: ClipSegment) -> str:
        proxy_marker = " [proxy]" if clip.proxy_path is not None else ""
        return (
            f"{clip.display_name}{proxy_marker} | "
            f"{_format_seconds(clip.start_seconds)} -> {_format_seconds(clip.effective_end or 0.0)}"
        )

    def _format_proxy_status(self, clip: ClipSegment) -> str:
        if clip.proxy_status == "ready" and clip.proxy_path is not None:
            return f"Preview source: proxy ({clip.proxy_path.name})"
        if clip.proxy_status == "building":
            return "Preview source: original clip while proxy builds"
        if clip.proxy_status == "queued":
            return "Preview source: original clip, proxy queued"
        if clip.proxy_status == "failed":
            return f"Preview source: original clip, proxy failed: {clip.proxy_error or 'unknown error'}"
        return "Preview source: original clip"

    def _append_log(self, message: str) -> None:
        self.log_output.configure(state="normal")
        self.log_output.insert(tk.END, f"{message}\n")
        self.log_output.see(tk.END)
        self.log_output.configure(state="disabled")

    def _poll_render_events(self) -> None:
        for event in self.render_controller.poll_events():
            if event.name == "job_started":
                self._on_job_started(event.args[0])
            elif event.name == "job_finished":
                self._on_job_finished(event.args[0])
            elif event.name == "job_failed":
                self._on_job_failed(event.args[0], event.args[1])
            elif event.name == "progress_message":
                self._on_render_progress_message(event.args[0], event.args[1])
            elif event.name == "progress_value":
                self._on_render_progress_value(event.args[0], event.args[1])
            elif event.name == "queue_empty":
                self._on_queue_empty()
            elif event.name == "queue_size_changed":
                self._on_queue_size_changed(event.args[0])
        self.root.after(150, self._poll_render_events)

    def _poll_proxy_events(self) -> None:
        for event in self.proxy_controller.poll_events():
            if event.name == "proxy_started":
                self._on_proxy_started(event.args[0])
            elif event.name == "proxy_finished":
                self._on_proxy_finished(event.args[0], event.args[1])
            elif event.name == "proxy_failed":
                self._on_proxy_failed(event.args[0], event.args[1])
        self.root.after(150, self._poll_proxy_events)

    def _poll_preview(self) -> None:
        image = self.preview_player.tick()
        if image is not None:
            self._set_preview_image(image)
            self._set_playhead_value(self.preview_player.playhead_seconds, self.preview_player.duration_seconds)
            self.preview_state_var.set("Playing" if self.preview_player.is_playing else "Paused")
            if not self.preview_player.is_playing:
                self.play_pause_button.configure(text="Play")
        self.root.after(33, self._poll_preview)

    def _on_job_started(self, queue_key: str) -> None:
        self._update_queue_item(queue_key, "Rendering")
        self.render_progress_var.set(0.0)
        self.render_progress_label_var.set(f"Rendering {Path(queue_key).name}")
        self._last_progress_message = ""
        self._append_log(f"Started {queue_key}")

    def _on_job_finished(self, queue_key: str) -> None:
        self._update_queue_item(queue_key, "Done")
        self.render_progress_var.set(100.0)
        self.render_progress_label_var.set(f"Finished {Path(queue_key).name}")
        self._append_log(f"Finished {queue_key}")
        self.queued_outputs.discard(Path(queue_key))

    def _on_job_failed(self, queue_key: str, error: str) -> None:
        self._update_queue_item(queue_key, "Failed")
        self.render_progress_label_var.set(f"Failed {Path(queue_key).name}")
        self._append_log(f"Failed {queue_key}: {error}")
        self.queued_outputs.discard(Path(queue_key))
        messagebox.showerror("Render failed", f"{queue_key}\n\n{error}", parent=self.root)

    def _on_render_progress_message(self, queue_key: str, message: str) -> None:
        display_name = Path(queue_key).name
        self.render_progress_label_var.set(f"{display_name}: {message}")
        if message != self._last_progress_message:
            self._append_log(message)
            self._last_progress_message = message

    def _on_render_progress_value(self, _queue_key: str, fraction: float) -> None:
        self.render_progress_var.set(max(0.0, min(fraction * 100.0, 100.0)))

    def _on_queue_empty(self) -> None:
        self.start_queue_button.configure(state="normal")
        if not self.render_controller.is_running:
            self.render_progress_label_var.set("Idle")
        self.status_var.set("Queue idle")

    def _on_queue_size_changed(self, remaining_jobs: int) -> None:
        if self.render_controller.is_running:
            self.status_var.set(f"Rendering queue ({remaining_jobs} waiting)")

    def _update_queue_item(self, queue_key: str, state: str) -> None:
        row = self.queue_rows.get(queue_key)
        if row is None:
            return
        output_name = Path(queue_key).name
        self.queue_listbox.delete(row)
        self.queue_listbox.insert(row, f"{state} | {output_name}")

    def _on_proxy_started(self, source_path_text: str) -> None:
        clip = self._find_clip_by_source(Path(source_path_text))
        if clip is None:
            return
        clip.proxy_status = "building"
        clip.proxy_error = None
        if clip == self._selected_clip():
            self.proxy_status_var.set(self._format_proxy_status(clip))

    def _on_proxy_finished(self, source_path_text: str, proxy_path_text: str) -> None:
        clip = self._find_clip_by_source(Path(source_path_text))
        if clip is None:
            return
        clip.proxy_path = Path(proxy_path_text)
        clip.proxy_status = "ready"
        clip.proxy_error = None
        self._refresh_clip_list_text()
        if clip == self._selected_clip():
            current_position = self.preview_player.playhead_seconds
            was_playing = self.preview_player.is_playing
            self._load_preview_for_clip(clip)
            self._seek_preview(min(current_position, clip.duration_seconds or current_position))
            if was_playing:
                self.preview_player.play()
                self.play_pause_button.configure(text="Pause")
                self.preview_state_var.set("Playing")

    def _on_proxy_failed(self, source_path_text: str, error: str) -> None:
        clip = self._find_clip_by_source(Path(source_path_text))
        if clip is None:
            return
        clip.proxy_status = "failed"
        clip.proxy_error = error
        if clip == self._selected_clip():
            self.proxy_status_var.set(self._format_proxy_status(clip))
        self._append_log(f"Proxy build failed for {clip.display_name}: {error}")

    def _find_clip_by_source(self, source_path: Path) -> ClipSegment | None:
        for clip in self.current_clips:
            if clip.source_path == source_path:
                return clip
        return None

    def _on_close(self) -> None:
        self.preview_player.release()
        self.proxy_controller.shutdown()
        self.render_controller.shutdown()
        self.root.destroy()


def _format_seconds(value: float) -> str:
    total_milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
