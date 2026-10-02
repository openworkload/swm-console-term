"""Interactive htop-style overview TUI for Sky Port."""

from __future__ import annotations

import curses
import os
import threading
import time
import typing
from enum import Enum
from io import BytesIO

from .common import (
    copy_to_clipboard,
    is_cloud_partition_manager,
    is_created_cloud_node,
    is_job_active,
    job_state_code,
    main_node_ip,
    sort_jobs_for_overview,
    truncate_details,
)

DEFAULT_INTERVAL = 5
# Top chrome: border, title, border.
STATS_HEIGHT = 3
STATUS_HEIGHT = 1
BUTTONS_HEIGHT = 1
# Status line + footer button hints reserved at the bottom of the screen.
FOOTER_HEIGHT = STATUS_HEIGHT + BUTTONS_HEIGHT
# Manual double-click window when the terminal does not report BUTTON1_DOUBLE_CLICKED.
DOUBLE_CLICK_SEC = 0.4

# Color pair ids
PAIR_TITLE = 1
PAIR_STATS = 2
PAIR_HEADER = 3
PAIR_SELECTED = 4
PAIR_STATE_R = 5
PAIR_STATE_Q = 6
PAIR_STATE_E = 7
PAIR_STATE_W = 8
PAIR_STATE_T = 9
PAIR_FOOTER = 10
PAIR_ERROR = 11
PAIR_BORDER = 12
PAIR_BUTTON = 13
PAIR_MODAL = 14
PAIR_SHADOW = 15
PAIR_MODAL_BORDER = 16
PAIR_STATUS = 17
PAIR_STATUS_ERROR = 18


def run_overview(swm_api: typing.Any, interval: float = DEFAULT_INTERVAL) -> None:
    """Enter the overview TUI (blocks until the user quits)."""
    # Read by ncurses at initscr time; default ~1000ms makes Esc much slower than q.
    os.environ.setdefault("ESCDELAY", "1")
    app = OverviewApp(swm_api, interval)
    curses.wrapper(app.main)


class OverviewApp:
    def __init__(self, swm_api: typing.Any, interval: float) -> None:
        self._api = swm_api
        self._interval = max(0.1, float(interval))
        self._lock = threading.Lock()
        self._jobs: list[typing.Any] = []
        self._selected = 0
        self._scroll = 0
        self._mode = "list"  # "list" | "detail"
        self._detail_job: typing.Any | None = None
        self._detail_id: str | None = None
        self._detail_stdout: str | None = None
        self._detail_stderr: str | None = None
        self._partitions = 0
        self._cloud_nodes = 0
        self._active_job_count = 0
        self._error: str | None = None
        self._last_refresh = 0.0
        self._refreshing = False
        self._ui_height = 0
        self._ui_width = 0
        self._last_click_idx = -1
        self._last_click_time = 0.0
        # (action, x_start, x_end) for the current footer button row; y is always footer.
        self._footer_hits: list[tuple[str, int, int]] = []
        self._status: str | None = None
        self._status_until = 0.0
        self._help_open = False
        # Close button hit box inside the help modal: (y, x0, x1)
        self._help_close_hit: tuple[int, int, int] | None = None
        # Modal rectangle (top, left, bottom, right) exclusive bottom/right for outside-click.
        self._help_rect: tuple[int, int, int, int] | None = None
        # Right-click context menu on a job row.
        self._ctx_open = False
        self._ctx_job_id: str | None = None
        self._ctx_hits: list[tuple[str, int, int, int]] = []  # (action, y, x0, x1)
        self._ctx_rect: tuple[int, int, int, int] | None = None
        self._ctx_anchor: tuple[int, int] = (0, 0)

    def main(self, stdscr: typing.Any) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        # Must run after initscr (curses.wrapper): default ESCDELAY is ~1000ms so
        # ncurses can distinguish Esc from arrow-key sequences. q has no such wait.
        try:
            curses.set_escdelay(1)
        except Exception:  # noqa: BLE001 - not available on all platforms
            pass
        self._init_colors()
        self._init_mouse()
        stdscr.nodelay(True)
        # Short poll so keys are handled quickly even during background refresh.
        stdscr.timeout(50)

        self._request_refresh()

        while True:
            now = time.monotonic()
            with self._lock:
                mode = self._mode
                last = self._last_refresh
                busy = self._refreshing
            if mode == "list" and not busy and now - last >= self._interval:
                self._request_refresh()
            elif mode == "detail" and not busy and now - last >= self._interval:
                self._request_detail_refresh()

            height, width = stdscr.getmaxyx()
            self._ui_height = height
            self._ui_width = width
            if height < 10 or width < 40:
                stdscr.erase()
                self._addstr(stdscr, 0, 0, "Terminal too small", curses.color_pair(PAIR_ERROR))
                stdscr.refresh()
            else:
                self._draw(stdscr, height, width)

            key = stdscr.getch()
            if key == -1:
                continue
            if self._handle_key(key):
                break

    def _init_mouse(self) -> None:
        """Leave terminal mouse tracking off so IP text can be drag-selected.

        Job list navigation, details, cancel, and copy use the keyboard
        (and footer key equivalents). Press c to copy the selected job IP(s).
        """
        try:
            curses.mousemask(0)
        except curses.error:
            pass

    def _init_colors(self) -> None:
        """NVIDIA-like palette: lime green chrome on dark, white stats."""
        if not curses.has_colors():
            return
        curses.start_color()

        # Prefer true NVIDIA green (#76B900) when the terminal lets us rewrite
        # the palette; otherwise use a 256-color lime close to that brand color.
        green = curses.COLOR_GREEN
        if curses.can_change_color():
            try:
                # curses RGB is 0..1000
                curses.init_color(curses.COLOR_GREEN, 463, 725, 0)
            except curses.error:
                pass
        elif curses.COLORS >= 256:
            green = 154  # xterm lime / yellow-green

        white = curses.COLOR_WHITE
        black = curses.COLOR_BLACK
        yellow = curses.COLOR_YELLOW
        red = curses.COLOR_RED
        # Soft gray for terminated jobs when 256 colors are available
        dim = 244 if curses.COLORS >= 256 else white

        # Header: light grey with a green tint (distinct from bright selection).
        header_fg = black
        header_bg = green
        if curses.can_change_color() and curses.COLORS > 16:
            try:
                # Soft sage grey (~#c5d0bc).
                curses.init_color(16, 773, 816, 737)
                header_bg = 16
            except curses.error:
                header_bg = 151 if curses.COLORS >= 256 else curses.COLOR_WHITE
        elif curses.COLORS >= 256:
            header_bg = 151  # xterm pale green-grey
        else:
            header_fg = black
            header_bg = white

        curses.init_pair(PAIR_TITLE, green, -1)
        curses.init_pair(PAIR_STATS, white, -1)
        curses.init_pair(PAIR_HEADER, header_fg, header_bg)
        curses.init_pair(PAIR_SELECTED, black, green)
        curses.init_pair(PAIR_STATE_R, green, -1)
        curses.init_pair(PAIR_STATE_Q, yellow, -1)
        curses.init_pair(PAIR_STATE_E, red, -1)
        curses.init_pair(PAIR_STATE_W, white, -1)
        curses.init_pair(PAIR_STATE_T, dim, -1)
        # Footer bar matches the job-list background (terminal default).
        curses.init_pair(PAIR_FOOTER, green, -1)
        curses.init_pair(PAIR_ERROR, red, -1)
        curses.init_pair(PAIR_BORDER, green, -1)
        # Status line: dark gray bar, slightly brighter than the default list bg.
        status_bg = curses.COLOR_BLACK
        if curses.can_change_color() and curses.COLORS > 16:
            try:
                # ~#3a3a3a -- darker than header sage, brighter than pure black.
                curses.init_color(21, 230, 230, 230)
                status_bg = 21
            except curses.error:
                status_bg = 238 if curses.COLORS >= 256 else curses.COLOR_BLACK
        elif curses.COLORS >= 256:
            status_bg = 238  # xterm Grey27
        curses.init_pair(PAIR_STATUS, white, status_bg)
        curses.init_pair(PAIR_STATUS_ERROR, red, status_bg)
        # Clickable footer buttons: dark text on sage (or white) so they read as controls.
        button_bg = header_bg if header_bg != green else white
        curses.init_pair(PAIR_BUTTON, black, button_bg)
        # Help modal: blue panel with dark-blue drop shadow.
        if curses.can_change_color() and curses.COLORS > 16:
            try:
                curses.init_color(18, 220, 420, 850)  # medium blue
                curses.init_color(19, 40, 80, 280)  # dark blue shadow
                modal_bg, shadow_bg = 18, 19
            except curses.error:
                modal_bg = 27 if curses.COLORS >= 256 else curses.COLOR_BLUE
                shadow_bg = 17 if curses.COLORS >= 256 else black
        elif curses.COLORS >= 256:
            modal_bg = 27  # DodgerBlue3
            shadow_bg = 17  # DarkBlue
        else:
            modal_bg = curses.COLOR_BLUE
            shadow_bg = black
        curses.init_pair(PAIR_MODAL, white, modal_bg)
        curses.init_pair(PAIR_SHADOW, white, shadow_bg)
        # Light gray frame on the modal blue background.
        if curses.can_change_color() and curses.COLORS > 16:
            try:
                curses.init_color(20, 780, 780, 780)  # light gray
                border_fg = 20
            except curses.error:
                border_fg = 250 if curses.COLORS >= 256 else white
        elif curses.COLORS >= 256:
            border_fg = 250
        else:
            border_fg = white
        curses.init_pair(PAIR_MODAL_BORDER, border_fg, modal_bg)

    def _request_refresh(self) -> None:
        """Fetch overview data in a background thread (never blocks the UI)."""
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def worker() -> None:
            try:
                self._fetch_overview()
            finally:
                with self._lock:
                    self._refreshing = False
                    self._last_refresh = time.monotonic()

        threading.Thread(target=worker, name="swm-overview-refresh", daemon=True).start()

    def _request_detail_refresh(self) -> None:
        with self._lock:
            if self._refreshing or not self._detail_id:
                return
            detail_id = self._detail_id
            self._refreshing = True

        def worker() -> None:
            try:
                self._fetch_detail(detail_id)
            finally:
                with self._lock:
                    self._refreshing = False
                    self._last_refresh = time.monotonic()

        threading.Thread(target=worker, name="swm-overview-detail", daemon=True).start()

    def _fetch_overview(self) -> None:
        with self._lock:
            prev_id = None
            if self._jobs and 0 <= self._selected < len(self._jobs):
                prev_id = getattr(self._jobs[self._selected], "id", None)

        try:
            nodes = self._api.get_nodes()
            jobs = self._api.get_jobs()

            if isinstance(nodes, list):
                partitions = sum(1 for n in nodes if is_cloud_partition_manager(n))
                cloud_nodes = sum(1 for n in nodes if is_created_cloud_node(n))
            else:
                partitions = 0
                cloud_nodes = 0

            if isinstance(jobs, list):
                ordered = sort_jobs_for_overview(jobs)
                active_count = sum(1 for j in ordered if is_job_active(j))
            else:
                ordered = []
                active_count = 0
            error = None
        except Exception as exc:  # noqa: BLE001 - show any API/network failure in the TUI
            with self._lock:
                self._error = str(exc)
            return

        with self._lock:
            # Do not clobber the list snapshot while the user is in details.
            if self._mode == "detail":
                return
            self._partitions = partitions
            self._cloud_nodes = cloud_nodes
            self._jobs = ordered
            self._active_job_count = active_count
            self._error = error
            if prev_id and self._jobs:
                for idx, job in enumerate(self._jobs):
                    if getattr(job, "id", None) == prev_id:
                        self._selected = idx
                        break
                else:
                    self._selected = min(self._selected, max(0, len(self._jobs) - 1))
            elif not self._jobs:
                self._selected = 0
            else:
                self._selected = max(0, min(self._selected, len(self._jobs) - 1))

    def _fetch_detail(self, detail_id: str) -> None:
        try:
            detail = self._api.get_job(detail_id)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._detail_id == detail_id:
                    self._error = str(exc)
            return

        stdout_text = self._read_job_stream(detail_id, "stdout")
        stderr_text = self._read_job_stream(detail_id, "stderr")

        with self._lock:
            if self._mode != "detail" or self._detail_id != detail_id:
                return
            if detail is not None:
                self._detail_job = detail
                self._detail_stdout = stdout_text
                self._detail_stderr = stderr_text
                self._error = None
            else:
                # Job gone; keep list snapshot and return to it.
                self._mode = "list"
                self._detail_job = None
                self._detail_id = None
                self._detail_stdout = None
                self._detail_stderr = None

    def _read_job_stream(self, job_id: str, stream: str) -> str:
        """Fetch job stdout/stderr via API (script log + Task N sections)."""
        label = stream
        try:
            if stream == "stdout":
                file_obj = self._api.get_job_stdout(job_id)
            else:
                file_obj = self._api.get_job_stderr(job_id)
        except Exception as exc:  # noqa: BLE001 - show in detail pane
            return f"({label} unavailable: {exc})"
        if file_obj is None:
            return f"(no {label} yet)"
        payload = getattr(file_obj, "payload", None)
        if payload is None:
            return f"(no {label} yet)"
        try:
            raw = payload.read()
        except Exception as exc:  # noqa: BLE001
            return f"({label} unavailable: {exc})"
        if not raw:
            return f"(no {label} yet)"
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def _leave_detail(self) -> None:
        """Return from job details to the overview job list."""
        self._close_context_menu()
        with self._lock:
            self._mode = "list"
            self._detail_job = None
            self._detail_id = None
            self._detail_stdout = None
            self._detail_stderr = None

    def _open_detail(self, job: typing.Any) -> None:
        """Switch to detail mode for job (snapshot first, then background refresh)."""
        self._close_context_menu()
        job_id = getattr(job, "id", None)
        if not job_id:
            return
        with self._lock:
            self._detail_id = str(job_id)
            self._detail_job = job
            self._detail_stdout = None
            self._detail_stderr = None
            self._mode = "detail"
        self._request_detail_refresh()

    def _job_index_at_row(self, y: int) -> int | None:
        """Map screen row to job index in the list, or None if not on a job row."""
        first_data = STATS_HEIGHT + 1
        footer_y = max(0, self._ui_height - FOOTER_HEIGHT)
        if y < first_data or y >= footer_y:
            return None
        with self._lock:
            scroll = self._scroll
            n_jobs = len(self._jobs)
        job_idx = scroll + (y - first_data)
        if 0 <= job_idx < n_jobs:
            return job_idx
        return None

    def _footer_actions(self, mode: str) -> list[tuple[str, str]]:
        """Return (key_hint, label) pairs for the footer bar."""
        if mode == "detail":
            return [
                ("q", "Back"),
                ("Del", "Cancel"),
                ("s", "Submit"),
                ("r", "Refresh"),
                ("?", "Help"),
            ]
        return [
            ("q", "Quit"),
            ("Enter", "Details"),
            ("Del", "Cancel"),
            ("s", "Submit"),
            ("r", "Refresh"),
            ("?", "Help"),
        ]

    def _help_lines(self) -> list[str]:
        """Keyboard map shown in the help modal."""
        return [
            "Navigation",
            "  Up/Down  j/k     Select job",
            "  PgUp/PgDn        Jump 10 jobs",
            "  Home/End         First / last job",
            "",
            "Actions",
            "  Enter            Open job details",
            "  Del              Cancel selected job",
            "  s                Resubmit selected job script",
            "  c                Copy main / node IP(s)",
            "  r                Refresh",
            "  q / Esc          Quit (list) or Back (details)",
            "  ? / h            Open this help",
            "",
            "IP addresses are normal text: drag-select with",
            "the mouse to copy, or press c for clipboard.",
            "Press Esc to dismiss this help.",
        ]

    def _close_context_menu(self) -> None:
        self._ctx_open = False
        self._ctx_job_id = None
        self._ctx_hits = []
        self._ctx_rect = None

    def _open_context_menu(self, job_idx: int, x: int, y: int) -> None:
        with self._lock:
            jobs = self._jobs
            if not (0 <= job_idx < len(jobs)):
                return
            job = jobs[job_idx]
            job_id = getattr(job, "id", None)
            self._selected = job_idx
        if not job_id:
            return
        self._help_open = False
        self._ctx_open = True
        self._ctx_job_id = str(job_id)
        self._ctx_anchor = (x, y)

    def _job_script_content(self, job: typing.Any) -> str | None:
        """Return job script text from a Job object (full get_job includes script_content)."""
        script = getattr(job, "script_content", None)
        if script:
            return str(script)
        extra = getattr(job, "additional_properties", None) or {}
        if isinstance(extra, dict) and extra.get("script_content"):
            return str(extra["script_content"])
        if hasattr(job, "get") and "script_content" in job:
            return str(job["script_content"])
        return None

    def _selected_job_id(self) -> str | None:
        """Return the selected job id (list selection or open detail)."""
        with self._lock:
            if self._mode == "detail":
                job_id = self._detail_id or getattr(self._detail_job, "id", None)
                return str(job_id) if job_id else None
            jobs = self._jobs
            selected = self._selected
            if jobs and 0 <= selected < len(jobs):
                job_id = getattr(jobs[selected], "id", None)
                return str(job_id) if job_id else None
        return None

    def _resubmit_job(self, job_id: str) -> None:
        """Submit a new job using the same script_content as job_id."""

        def worker() -> None:
            try:
                self._flash_status(f"Resubmitting job {job_id}...")
                detail = self._api.get_job(job_id)
                if detail is None:
                    with self._lock:
                        self._error = f"Resubmit failed: job {job_id} not found"
                    return
                script = self._job_script_content(detail)
                if not script:
                    with self._lock:
                        self._error = f"Resubmit failed: no script_content for {job_id}"
                    return
                result = self._api.submit_job(BytesIO(script.encode("utf-8")))
                new_id = None
                if result is not None:
                    payload = getattr(result, "payload", None)
                    if payload is not None:
                        raw = payload.read()
                        if isinstance(raw, bytes):
                            new_id = raw.decode("utf-8", errors="replace").strip()
                        elif raw:
                            new_id = str(raw).strip()
                if new_id:
                    self._flash_status(f"Resubmitted {job_id} as {new_id}", seconds=4.0)
                else:
                    self._flash_status(f"Resubmitted {job_id} (no id in response)", seconds=4.0)
                with self._lock:
                    self._error = None
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._error = f"Resubmit failed for {job_id}: {exc}"
            self._request_refresh()

        threading.Thread(target=worker, name="swm-overview-resubmit", daemon=True).start()

    def _run_context_action(self, action: str) -> bool:
        job_id = self._ctx_job_id
        self._close_context_menu()
        if not job_id:
            return False
        if action == "cancel":
            with self._lock:
                jobs = self._jobs
                job = next((j for j in jobs if str(getattr(j, "id", "")) == job_id), None)
            self._cancel_job_if_not_finished(job)
            return False
        if action == "resubmit":
            self._resubmit_job(job_id)
            return False
        return False

    def _run_action(self, action: str) -> bool:
        """Run a footer/keyboard action. Return True to quit the overview."""
        if action == "help":
            self._close_context_menu()
            self._help_open = True
            return False
        if action == "quit":
            return True
        if action == "back":
            self._leave_detail()
            return False
        if action == "refresh":
            if self._mode == "detail":
                self._request_detail_refresh()
            else:
                self._request_refresh()
            return False
        if action == "details":
            with self._lock:
                jobs = self._jobs
                selected = self._selected
            if jobs and 0 <= selected < len(jobs):
                self._open_detail(jobs[selected])
            return False
        if action == "submit":
            job_id = self._selected_job_id()
            if not job_id:
                self._flash_status("No job selected to resubmit")
                return False
            self._resubmit_job(job_id)
            return False
        if action == "cancel":
            with self._lock:
                if self._mode == "detail":
                    job = self._detail_job
                else:
                    jobs = self._jobs
                    selected = self._selected
                    job = jobs[selected] if jobs and 0 <= selected < len(jobs) else None
            self._cancel_job_if_not_finished(job)
            return False
        return False

    def _footer_button_at(self, x: int, y: int) -> str | None:
        """Return action id if (x, y) hits a footer button."""
        buttons_y = max(0, self._ui_height - BUTTONS_HEIGHT)
        if y != buttons_y:
            return None
        for action, x0, x1 in self._footer_hits:
            if x0 <= x < x1:
                return action
        return None

    def _flash_status(self, msg: str, seconds: float = 2.5) -> None:
        with self._lock:
            self._status = msg
            self._status_until = time.monotonic() + seconds

    def _copy_ip(self, ip: str) -> None:
        if copy_to_clipboard(ip):
            self._flash_status(f"Copied IP: {ip}")
        else:
            self._flash_status(f"Copy failed (select manually): {ip}")

    def _copy_visible_ips(self) -> None:
        """Copy main IP (list) or all node IPs (detail) for the current job."""
        with self._lock:
            mode = self._mode
            if mode == "detail":
                job = self._detail_job
            else:
                jobs = self._jobs
                selected = self._selected
                job = jobs[selected] if jobs and 0 <= selected < len(jobs) else None
        if job is None:
            self._flash_status("No job to copy IP from")
            return
        if mode == "detail":
            ips = [str(ip) for ip in (getattr(job, "node_ips", None) or []) if ip]
            if not ips:
                main = main_node_ip(job)
                ips = [main] if main else []
            text = ", ".join(ips)
        else:
            text = main_node_ip(job)
        if not text:
            self._flash_status("No node IP for this job")
            return
        self._copy_ip(text)

    def _ctx_action_at(self, x: int, y: int) -> str | None:
        for action, cy, x0, x1 in self._ctx_hits:
            if y == cy and x0 <= x < x1:
                return action
        return None

    def _handle_mouse(self) -> bool:
        """Handle mouse clicks. Return True to quit."""
        try:
            _id, x, y, _z, bstate = curses.getmouse()
        except curses.error:
            return False

        left_clicked = bool(
            (getattr(curses, "BUTTON1_CLICKED", 0) & bstate)
            or (getattr(curses, "BUTTON1_PRESSED", 0) & bstate)
            or (getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0) & bstate)
        )
        right_clicked = bool(
            (getattr(curses, "BUTTON3_CLICKED", 0) & bstate)
            or (getattr(curses, "BUTTON3_PRESSED", 0) & bstate)
            or (getattr(curses, "BUTTON3_RELEASED", 0) & bstate)
        )

        if self._help_open:
            if not left_clicked:
                return False
            if self._help_close_hit is not None:
                cy, x0, x1 = self._help_close_hit
                if y == cy and x0 <= x < x1:
                    self._help_open = False
                    return False
            if self._help_rect is not None:
                top, left, bottom, right = self._help_rect
                if not (top <= y < bottom and left <= x < right):
                    self._help_open = False
            return False

        if self._ctx_open:
            if right_clicked or left_clicked:
                action = self._ctx_action_at(x, y)
                if action is not None:
                    return self._run_context_action(action)
                if self._ctx_rect is not None:
                    top, left, bottom, right = self._ctx_rect
                    if not (top <= y < bottom and left <= x < right):
                        self._close_context_menu()
            return False

        # Wheel scroll on the job list (when reported as button 4/5).
        btn4 = getattr(curses, "BUTTON4_PRESSED", 0)
        btn5 = getattr(curses, "BUTTON5_PRESSED", 0)
        if self._mode == "list":
            if btn4 and bstate & btn4:
                with self._lock:
                    if self._selected > 0:
                        self._selected -= 1
                return False
            if btn5 and bstate & btn5:
                with self._lock:
                    if self._selected < max(0, len(self._jobs) - 1):
                        self._selected += 1
                return False

        if right_clicked and self._mode == "list":
            job_idx = self._job_index_at_row(y)
            if job_idx is not None:
                self._open_context_menu(job_idx, x, y)
            return False

        if not left_clicked:
            return False

        action = self._footer_button_at(x, y)
        if action is not None:
            return self._run_action(action)

        if self._mode != "list":
            return False

        job_idx = self._job_index_at_row(y)
        if job_idx is None:
            return False

        double = bool(getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0) & bstate)
        now = time.monotonic()
        if not double and job_idx == self._last_click_idx and now - self._last_click_time <= DOUBLE_CLICK_SEC:
            double = True

        with self._lock:
            jobs = self._jobs
            self._selected = job_idx
        self._last_click_idx = job_idx
        self._last_click_time = 0.0 if double else now

        if double and jobs and 0 <= job_idx < len(jobs):
            self._open_detail(jobs[job_idx])
        return False

    def _handle_key(self, key: int) -> bool:
        """Return True to quit. Must stay non-blocking."""
        if key == curses.KEY_MOUSE:
            return self._handle_mouse()

        if self._help_open:
            if key in (ord("q"), ord("Q"), 27, curses.KEY_ENTER, 10, 13, ord(" "), ord("h"), ord("H"), ord("?")):
                self._help_open = False
            return False

        if self._ctx_open:
            if key in (27, ord("q"), ord("Q")):
                self._close_context_menu()
                return False
            if key in (ord("c"), ord("C")):
                return self._run_context_action("cancel")
            if key in (ord("r"), ord("R")):
                # In menu, r = resubmit (not refresh).
                return self._run_context_action("resubmit")
            if key in (curses.KEY_ENTER, 10, 13):
                return self._run_context_action("cancel")
            return False

        if key in (ord("?"), ord("h"), ord("H")):
            return self._run_action("help")

        if key in (ord("r"), ord("R")):
            return self._run_action("refresh")

        if key in (ord("s"), ord("S")):
            return self._run_action("submit")

        if key in (ord("c"), ord("C")):
            self._copy_visible_ips()
            return False

        if self._mode == "detail":
            if key in (ord("q"), ord("Q"), 27, curses.KEY_BACKSPACE, ord("b"), ord("B")):
                return self._run_action("back")
            if key == curses.KEY_DC:
                return self._run_action("cancel")
            return False

        if key in (ord("q"), ord("Q"), 27):
            return self._run_action("quit")

        with self._lock:
            jobs = self._jobs
            selected = self._selected

        if key in (curses.KEY_UP, ord("k")):
            if selected > 0:
                with self._lock:
                    self._selected -= 1
        elif key in (curses.KEY_DOWN, ord("j")):
            if selected < len(jobs) - 1:
                with self._lock:
                    self._selected += 1
        elif key == curses.KEY_PPAGE:
            with self._lock:
                self._selected = max(0, self._selected - 10)
        elif key == curses.KEY_NPAGE:
            with self._lock:
                self._selected = min(max(0, len(self._jobs) - 1), self._selected + 10)
        elif key == curses.KEY_HOME:
            with self._lock:
                self._selected = 0
        elif key == curses.KEY_END:
            with self._lock:
                self._selected = max(0, len(self._jobs) - 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            return self._run_action("details")
        elif key == curses.KEY_DC:
            return self._run_action("cancel")
        return False

    def _cancel_job_if_not_finished(self, job: typing.Any | None) -> None:
        """Cancel job via API unless it is already finished (state F)."""
        if job is None:
            with self._lock:
                self._error = "No job selected to cancel"
            return
        job_id = getattr(job, "id", None)
        if not job_id:
            with self._lock:
                self._error = "Selected job has no id"
            return
        code = job_state_code(job)
        if code == "F":
            with self._lock:
                self._error = f"Job {job_id} is finished; not canceled"
            return

        with self._lock:
            self._error = f"Canceling job {job_id}..."

        def worker() -> None:
            try:
                self._api.cancel_job(str(job_id))
                with self._lock:
                    self._error = f"Canceled job {job_id}"
            except Exception as exc:  # noqa: BLE001 - show in TUI
                with self._lock:
                    self._error = f"Cancel failed for {job_id}: {exc}"
            # Refresh list (and detail if still open on this job).
            with self._lock:
                mode = self._mode
                detail_id = self._detail_id
            if mode == "detail" and detail_id == str(job_id):
                self._request_detail_refresh()
            else:
                self._request_refresh()

        threading.Thread(target=worker, name="swm-overview-cancel", daemon=True).start()

    def _draw(self, stdscr: typing.Any, height: int, width: int) -> None:
        with self._lock:
            mode = self._mode
            jobs = list(self._jobs)
            selected = self._selected
            scroll = self._scroll
            partitions = self._partitions
            cloud_nodes = self._cloud_nodes
            active_job_count = self._active_job_count
            error = self._error
            detail_job = self._detail_job
            detail_stdout = self._detail_stdout
            detail_stderr = self._detail_stderr
            refreshing = self._refreshing

        visible = max(1, height - FOOTER_HEIGHT - STATS_HEIGHT - 1)
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + visible:
            scroll = selected - visible + 1
        scroll = max(0, scroll)
        with self._lock:
            self._scroll = scroll

        stdscr.erase()
        status = self._status if time.monotonic() < self._status_until else None
        if status is None:
            self._status = None
        self._draw_stats(stdscr, width)
        if mode == "detail":
            self._draw_detail(stdscr, height, width, detail_job, detail_stdout, detail_stderr)
        else:
            self._draw_job_table(stdscr, height, width, jobs, selected, scroll)
        self._draw_status_line(
            stdscr,
            height,
            width,
            mode,
            error,
            refreshing,
            status,
            partitions,
            cloud_nodes,
            active_job_count,
        )
        self._draw_footer(stdscr, height, width, mode)
        if self._ctx_open:
            self._draw_context_menu(stdscr, height, width)
        else:
            self._ctx_hits = []
            self._ctx_rect = None
        if self._help_open:
            self._draw_help_modal(stdscr, height, width)
        else:
            self._help_close_hit = None
            self._help_rect = None
        stdscr.refresh()

    def _draw_modal_chrome(
        self,
        stdscr: typing.Any,
        top: int,
        left: int,
        bottom: int,
        right: int,
        height: int,
        width: int,
    ) -> None:
        """Fill, tight drop shadow, and light-gray border shared by help and context menu."""
        box_w = right - left
        box_h = bottom - top
        shadow = curses.color_pair(PAIR_SHADOW)
        modal = curses.color_pair(PAIR_MODAL)
        border = curses.color_pair(PAIR_MODAL_BORDER)

        # Small offset so the shadow sits close to the panel.
        sh_dy, sh_dx = 1, 1
        for sy in range(top + sh_dy, min(bottom + sh_dy, height - 1)):
            sx = left + sh_dx
            n = max(0, min(box_w, width - sx - 1))
            if n > 0:
                self._addstr(stdscr, sy, sx, " " * n, shadow)

        for row in range(top, bottom):
            self._addstr(stdscr, row, left, " " * max(0, box_w - 1), modal)

        try:
            stdscr.attron(border)
            if bottom - 1 < height and right - 1 < width:
                stdscr.vline(top, left, curses.ACS_VLINE, box_h)
                stdscr.vline(top, right - 1, curses.ACS_VLINE, box_h)
                stdscr.hline(top, left, curses.ACS_HLINE, box_w)
                stdscr.hline(bottom - 1, left, curses.ACS_HLINE, box_w)
                stdscr.addch(top, left, curses.ACS_ULCORNER)
                stdscr.addch(top, right - 1, curses.ACS_URCORNER)
                stdscr.addch(bottom - 1, left, curses.ACS_LLCORNER)
                stdscr.addch(bottom - 1, right - 1, curses.ACS_LRCORNER)
            stdscr.attroff(border)
        except curses.error:
            pass

    def _draw_context_menu(self, stdscr: typing.Any, height: int, width: int) -> None:
        """Popup under the pointer: Cancel / Resubmit (same style as the help window)."""
        items = [("cancel", "Cancel"), ("resubmit", "Resubmit")]
        label_w = max(len(label) for _, label in items) + 2
        box_w = label_w + 2
        box_h = len(items) + 2
        ax, ay = self._ctx_anchor
        # Prefer below-right of the pointer; clamp into the usable pane.
        top = min(max(STATS_HEIGHT, ay + 1), max(STATS_HEIGHT, height - FOOTER_HEIGHT - box_h))
        left = min(max(0, ax), max(0, width - box_w - 1))
        bottom = top + box_h
        right = left + box_w
        self._ctx_rect = (top, left, bottom, right)

        self._draw_modal_chrome(stdscr, top, left, bottom, right, height, width)
        modal = curses.color_pair(PAIR_MODAL)

        hits: list[tuple[str, int, int, int]] = []
        for i, (action, label) in enumerate(items):
            row = top + 1 + i
            text = f" {label:<{label_w - 1}}"
            self._addstr(stdscr, row, left + 1, self._clip(text, box_w - 2), modal | curses.A_BOLD)
            hits.append((action, row, left + 1, left + 1 + len(text.rstrip()) + 1))
        self._ctx_hits = hits

    def _draw_help_modal(self, stdscr: typing.Any, height: int, width: int) -> None:
        """Centered help dialog with a drop shadow over the current view."""
        lines = self._help_lines()
        title = "Help"
        close_label = "[Close]"
        # +6: top border, title, blank, body, blank before Close, Close, bottom border
        inner_w = max(len(title), len(close_label), *(len(ln) for ln in lines)) + 4
        box_h = len(lines) + 6
        box_w = min(inner_w + 2, max(20, width - 4))
        box_h = min(box_h, max(8, height - 4))

        top = max(1, (height - box_h) // 2)
        left = max(1, (width - box_w) // 2)
        bottom = top + box_h
        right = left + box_w
        self._help_rect = (top, left, bottom, right)

        self._draw_modal_chrome(stdscr, top, left, bottom, right, height, width)
        modal = curses.color_pair(PAIR_MODAL)
        title_attr = curses.color_pair(PAIR_MODAL) | curses.A_BOLD
        btn = curses.color_pair(PAIR_BUTTON) | curses.A_BOLD

        self._addstr(stdscr, top + 1, left + 2, self._clip(title, box_w - 4), title_attr)

        close_y = bottom - 2
        body_top = top + 3
        # Leave a blank line above the Close button.
        max_body = max(0, close_y - 1 - body_top)
        for i, line in enumerate(lines[:max_body]):
            self._addstr(stdscr, body_top + i, left + 2, self._clip(line, box_w - 4), modal)

        close_x = left + max(2, (box_w - len(close_label)) // 2)
        self._addstr(stdscr, close_y, close_x, close_label, btn)
        self._help_close_hit = (close_y, close_x, close_x + len(close_label))

    def _draw_stats(self, stdscr: typing.Any, width: int) -> None:
        border = curses.color_pair(PAIR_BORDER) | curses.A_BOLD
        title = curses.color_pair(PAIR_TITLE) | curses.A_BOLD

        self._hline(stdscr, 0, 0, width, border)
        self._addstr(stdscr, 1, 2, "Sky Port Overview", title)
        self._hline(stdscr, STATS_HEIGHT - 1, 0, width, border)

    def _status_message(
        self,
        mode: str,
        error: str | None,
        refreshing: bool,
        status_msg: str | None,
        partitions: int,
        cloud_nodes: int,
        active_jobs: int,
    ) -> tuple[str, str, int]:
        """Return (left_text, right_text, color_pair) for the bottom status line.

        ``right_text`` is the refresh/update label (drawn right-aligned).
        """
        refresh_lbl = "updating..." if refreshing else f"every {self._interval:g}s"
        stats = (
            f"Cloud partitions: {partitions}  "
            f"Active jobs: {active_jobs}  "
            f"Cloud nodes: {cloud_nodes}"
        )
        if error:
            left, pair = f"Error: {error}", PAIR_STATUS_ERROR
        elif status_msg:
            left, pair = status_msg, PAIR_STATUS
        else:
            left = "Job details" if mode == "detail" else "All jobs"
            pair = PAIR_STATUS
        return f"{left}  |  {stats}", refresh_lbl, pair

    def _draw_status_line(
        self,
        stdscr: typing.Any,
        height: int,
        width: int,
        mode: str,
        error: str | None,
        refreshing: bool,
        status_msg: str | None,
        partitions: int,
        cloud_nodes: int,
        active_jobs: int,
    ) -> None:
        """One line above the footer buttons: mode, stats, refresh, errors, flashes."""
        y = height - FOOTER_HEIGHT
        left, right, pair = self._status_message(
            mode, error, refreshing, status_msg, partitions, cloud_nodes, active_jobs
        )
        attr = curses.color_pair(pair) | curses.A_BOLD
        self._addstr(stdscr, y, 0, " " * max(0, width - 1), attr)
        # Leave room for a space + right label so left never overlaps it.
        right_w = len(right)
        left_max = max(0, width - 2 - right_w - 1)
        self._addstr(stdscr, y, 1, self._clip(left, left_max), attr)
        if right_w and width > right_w + 1:
            self._addstr(stdscr, y, width - 1 - right_w, right, attr)

    def _draw_job_table(
        self,
        stdscr: typing.Any,
        height: int,
        width: int,
        jobs: list[typing.Any],
        selected: int,
        scroll: int,
    ) -> None:
        header_row = STATS_HEIGHT
        first_data = header_row + 1
        visible = max(1, height - FOOTER_HEIGHT - first_data)

        headers = ["ID", "Submit", "Start", "End", "Main IP", "State", "Details"]
        widths = self._column_widths(width)
        header_line = self._format_row(headers, widths)
        self._addstr(
            stdscr, header_row, 0, self._clip(header_line, width), curses.color_pair(PAIR_HEADER) | curses.A_BOLD
        )

        if not jobs:
            self._addstr(stdscr, first_data, 2, "No active jobs", curses.color_pair(PAIR_TITLE))
            return

        end = min(len(jobs), scroll + visible)
        for view_idx, job_idx in enumerate(range(scroll, end)):
            job = jobs[job_idx]
            ip = main_node_ip(job)
            row = [
                str(job.id),
                str(job.submit_time or ""),
                str(job.start_time or ""),
                str(job.end_time or ""),
                ip,
                self._state_str(job),
                truncate_details(job.state_details, max_len=max(10, widths[-1])),
            ]
            line = self._clip(self._format_row(row, widths), width)
            y = first_data + view_idx
            if job_idx == selected:
                base_attr = curses.color_pair(PAIR_SELECTED) | curses.A_BOLD
                self._addstr(stdscr, y, 0, line.ljust(width - 1), base_attr)
            else:
                base_attr = self._state_attr(job)
                self._addstr(stdscr, y, 0, line, base_attr)

    def _draw_detail(
        self,
        stdscr: typing.Any,
        height: int,
        width: int,
        job: typing.Any | None,
        stdout_text: str | None,
        stderr_text: str | None = None,
    ) -> None:
        y = STATS_HEIGHT
        bottom = height - FOOTER_HEIGHT
        if job is None:
            self._addstr(stdscr, y, 2, "No job details", curses.color_pair(PAIR_ERROR))
            return

        details = job.state_details or ""
        node_ips = ", ".join(str(ip) for ip in (job.node_ips or []) if ip)
        rows = [
            ("ID", str(job.id)),
            ("Submit", str(job.submit_time or "")),
            ("Start", str(job.start_time or "")),
            ("End", str(job.end_time or "")),
            ("Node IPs", node_ips),
            ("State", self._state_str(job)),
        ]
        label_w = 10
        value_x = 2 + label_w + 2
        for label, value in rows:
            if y >= bottom - 1:
                break
            self._addstr(stdscr, y, 2, f"{label:<{label_w}}", curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
            self._addstr(
                stdscr,
                y,
                value_x,
                self._clip(str(value), width - label_w - 5),
                self._state_attr(job) if label == "State" else 0,
            )
            y += 1

        # Show full multiline Details (wrap to width; no mid-line "..."). Reserve a
        # few rows for Stdout/Stderr when the pane is tall enough.
        y += 1
        if y < bottom:
            self._addstr(stdscr, y, 2, "Details", curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
            y += 1
        avail = max(0, bottom - y)
        # Two stream titles + at least one line each when possible.
        min_stream = 4 if avail > 6 else 0
        detail_budget = max(0, avail - min_stream)
        wrapped = self._wrap_text(str(details), max(1, width - 5))
        for line in wrapped[:detail_budget] or [""]:
            if y >= bottom:
                break
            self._addstr(stdscr, y, 4, line, 0)
            y += 1

        # Split remaining rows between stdout and stderr (stdout gets more when odd).
        remaining = max(0, bottom - y - 2)  # leave room for two section titles
        if remaining <= 0:
            return
        out_budget = max(1, (remaining + 1) // 2)
        err_budget = max(1, remaining - out_budget)

        y = self._draw_stream_section(stdscr, y, bottom, width, "Stdout", stdout_text, "stdout", out_budget)
        self._draw_stream_section(stdscr, y, bottom, width, "Stderr", stderr_text, "stderr", err_budget)

    def _draw_stream_section(
        self,
        stdscr: typing.Any,
        y: int,
        bottom: int,
        width: int,
        title: str,
        text: str | None,
        stream: str,
        budget: int,
    ) -> int:
        """Draw a Stdout/Stderr block; return the next free row."""
        if y >= bottom:
            return y
        y += 1
        if y < bottom:
            self._addstr(stdscr, y, 2, title, curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
            y += 1
        rows_left = max(0, min(budget, bottom - y))
        if rows_left <= 0:
            return y
        if text is None:
            lines = [f"(loading {stream}...)"]
        else:
            lines = str(text).splitlines() or [f"(no {stream} yet)"]
        task_suffix = f" {stream}:"
        for line in lines[-rows_left:]:
            attr = 0
            if line.startswith("Task ") and line.endswith(task_suffix):
                attr = curses.color_pair(PAIR_TITLE) | curses.A_BOLD
            elif line and set(line) <= {"-"}:
                attr = curses.color_pair(PAIR_TITLE)
            self._addstr(stdscr, y, 4, self._clip(line, width - 5), attr)
            y += 1
        return y

    def _draw_footer(self, stdscr: typing.Any, height: int, width: int, mode: str) -> None:
        y = height - BUTTONS_HEIGHT
        bar = curses.color_pair(PAIR_FOOTER)
        self._addstr(stdscr, y, 0, " " * max(0, width - 1), bar)

        # Keyboard hints only (mouse tracking is off so text stays selectable).
        self._footer_hits = []
        parts = [f"{label} ({key})" for key, label in self._footer_actions(mode)]
        text = "  ".join(parts)
        self._addstr(stdscr, y, 1, self._clip(text, width - 2), bar | curses.A_BOLD)

    def _column_widths(self, width: int) -> list[int]:
        # Prefer readable ID + state; give leftover to Details.
        base = [36, 20, 20, 20, 15, 6]
        used = sum(base) + len(base)  # spaces between columns
        details = max(12, width - used - 1)
        return base + [details]

    def _format_row(self, cols: list[str], widths: list[int]) -> str:
        parts = []
        for col, w in zip(cols, widths):
            text = col if len(col) <= w else col[: max(0, w - 3)] + "..."
            parts.append(f"{text:<{w}}")
        return " ".join(parts)

    def _state_str(self, job: typing.Any) -> str:
        state = getattr(job, "state", None)
        if isinstance(state, Enum):
            return str(state.value)
        return str(state or "")

    def _state_attr(self, job: typing.Any) -> int:
        code = job_state_code(job)
        mapping = {
            "R": PAIR_STATE_R,
            "Q": PAIR_STATE_Q,
            "E": PAIR_STATE_E,
            "W": PAIR_STATE_W,
            "T": PAIR_STATE_T,
        }
        pair = mapping.get(code)
        if pair is None:
            return 0
        return curses.color_pair(pair)

    def _clip(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."

    @staticmethod
    def _wrap_text(text: str, width: int) -> list[str]:
        """Soft-wrap each logical line to ``width``; preserve blank lines."""
        if width <= 0:
            return [""]
        out: list[str] = []
        for raw in text.splitlines() or [""]:
            if raw == "":
                out.append("")
                continue
            while len(raw) > width:
                out.append(raw[:width])
                raw = raw[width:]
            out.append(raw)
        return out

    def _addstr(self, win: typing.Any, y: int, x: int, text: str, attr: int = 0) -> None:
        try:
            height, width = win.getmaxyx()
            if y < 0 or y >= height or x >= width:
                return
            max_len = max(0, width - x - 1)
            win.addstr(y, x, text[:max_len], attr)
        except curses.error:
            pass

    def _hline(self, win: typing.Any, y: int, x: int, width: int, attr: int = 0) -> None:
        try:
            _, max_w = win.getmaxyx()
            n = max(0, min(width, max_w - x - 1))
            if n > 0:
                win.hline(y, x, curses.ACS_HLINE, n, attr)
        except curses.error:
            pass
