"""Interactive htop-style overview TUI for Sky Port."""

from __future__ import annotations

import curses
import os
import threading
import time
import typing
from enum import Enum

from .common import (
    is_cloud_partition_manager,
    is_created_cloud_node,
    is_job_active,
    job_state_code,
    main_node_ip,
    sort_jobs_for_overview,
    truncate_details,
)

DEFAULT_INTERVAL = 5
STATS_HEIGHT = 7
FOOTER_HEIGHT = 1

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
        self._partitions = 0
        self._cloud_nodes = 0
        self._active_job_count = 0
        self._error: str | None = None
        self._last_refresh = 0.0
        self._refreshing = False
        self._help = "q/Esc:quit  arrows/j/k:select  Enter:details  r:refresh"

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

        curses.init_pair(PAIR_TITLE, green, -1)
        curses.init_pair(PAIR_STATS, white, -1)
        curses.init_pair(PAIR_HEADER, black, green)
        curses.init_pair(PAIR_SELECTED, black, green)
        curses.init_pair(PAIR_STATE_R, green, -1)
        curses.init_pair(PAIR_STATE_Q, yellow, -1)
        curses.init_pair(PAIR_STATE_E, red, -1)
        curses.init_pair(PAIR_STATE_W, white, -1)
        curses.init_pair(PAIR_STATE_T, dim, -1)
        curses.init_pair(PAIR_FOOTER, black, green)
        curses.init_pair(PAIR_ERROR, red, -1)
        curses.init_pair(PAIR_BORDER, green, -1)

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

        with self._lock:
            if self._mode != "detail" or self._detail_id != detail_id:
                return
            if detail is not None:
                self._detail_job = detail
                self._error = None
            else:
                # Job gone; keep list snapshot and return to it.
                self._mode = "list"
                self._detail_job = None
                self._detail_id = None

    def _handle_key(self, key: int) -> bool:
        """Return True to quit. Must stay non-blocking."""
        if key in (ord("q"), ord("Q")):
            return True

        if key in (ord("r"), ord("R")):
            if self._mode == "detail":
                self._request_detail_refresh()
            else:
                self._request_refresh()
            return False

        if self._mode == "detail":
            if key in (27, curses.KEY_BACKSPACE, ord("b"), ord("B")):  # Esc / back
                with self._lock:
                    self._mode = "list"
                    self._detail_job = None
                    self._detail_id = None
                # Keep showing the cached list; next interval refresh updates it.
            return False

        if key == 27:  # Esc on the job list exits the overview
            return True

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
            if jobs and 0 <= selected < len(jobs):
                job = jobs[selected]
                job_id = getattr(job, "id", None)
                if job_id:
                    with self._lock:
                        self._detail_id = str(job_id)
                        self._detail_job = job
                        self._mode = "detail"
                    # Fetch fuller details in the background; UI already shows snapshot.
                    self._request_detail_refresh()
        return False

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
        self._draw_stats(stdscr, width, partitions, cloud_nodes, active_job_count, mode, error, refreshing)
        if mode == "detail":
            self._draw_detail(stdscr, height, width, detail_job)
        else:
            self._draw_job_table(stdscr, height, width, jobs, selected, scroll)
        self._draw_footer(stdscr, height, width, mode)
        stdscr.refresh()

    def _draw_stats(
        self,
        stdscr: typing.Any,
        width: int,
        partitions: int,
        cloud_nodes: int,
        active_jobs: int,
        mode: str,
        error: str | None,
        refreshing: bool,
    ) -> None:
        border = curses.color_pair(PAIR_BORDER) | curses.A_BOLD
        title = curses.color_pair(PAIR_TITLE) | curses.A_BOLD
        stats = curses.color_pair(PAIR_STATS) | curses.A_BOLD

        self._hline(stdscr, 0, 0, width, border)
        self._addstr(stdscr, 1, 2, "Sky Port Overview", title)
        status = "updating..." if refreshing else f"every {self._interval:g}s"
        self._addstr(stdscr, 1, max(2, width - len(status) - 2), status, curses.color_pair(PAIR_TITLE))

        line = (
            f"  Cloud partitions: {partitions}    "
            f"Active jobs: {active_jobs}    "
            f"Cloud nodes: {cloud_nodes}"
        )
        self._addstr(stdscr, 3, 0, self._clip(line, width), stats)

        if error:
            self._addstr(stdscr, 4, 2, self._clip(f"Error: {error}", width - 2), curses.color_pair(PAIR_ERROR))
        else:
            mode_label = "Job details" if mode == "detail" else "All jobs"
            self._addstr(stdscr, 4, 2, self._clip(mode_label, width - 2), curses.color_pair(PAIR_TITLE))

        self._hline(stdscr, STATS_HEIGHT - 1, 0, width, border)

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
            row = [
                str(job.id),
                str(job.submit_time or ""),
                str(job.start_time or ""),
                str(job.end_time or ""),
                main_node_ip(job),
                self._state_str(job),
                truncate_details(job.state_details, max_len=max(10, widths[-1])),
            ]
            line = self._clip(self._format_row(row, widths), width)
            y = first_data + view_idx
            if job_idx == selected:
                self._addstr(stdscr, y, 0, line.ljust(width - 1), curses.color_pair(PAIR_SELECTED) | curses.A_BOLD)
            else:
                attr = self._state_attr(job)
                self._addstr(stdscr, y, 0, line, attr)

    def _draw_detail(self, stdscr: typing.Any, height: int, width: int, job: typing.Any | None) -> None:
        y = STATS_HEIGHT
        if job is None:
            self._addstr(stdscr, y, 2, "No job details", curses.color_pair(PAIR_ERROR))
            return

        details = job.state_details or ""
        rows = [
            ("ID", str(job.id)),
            ("Submit", str(job.submit_time or "")),
            ("Start", str(job.start_time or "")),
            ("End", str(job.end_time or "")),
            ("Node IPs", ", ".join(job.node_ips or [])),
            ("State", self._state_str(job)),
        ]
        label_w = 10
        for label, value in rows:
            if y >= height - FOOTER_HEIGHT - 1:
                break
            self._addstr(stdscr, y, 2, f"{label:<{label_w}}", curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
            self._addstr(
                stdscr,
                y,
                2 + label_w + 2,
                self._clip(value, width - label_w - 5),
                self._state_attr(job) if label == "State" else 0,
            )
            y += 1

        y += 1
        if y < height - FOOTER_HEIGHT:
            self._addstr(stdscr, y, 2, "Details", curses.color_pair(PAIR_TITLE) | curses.A_BOLD)
            y += 1
        for line in str(details).splitlines() or [""]:
            if y >= height - FOOTER_HEIGHT:
                break
            self._addstr(stdscr, y, 4, self._clip(line, width - 5), 0)
            y += 1

    def _draw_footer(self, stdscr: typing.Any, height: int, width: int, mode: str) -> None:
        y = height - 1
        text = self._help
        if mode == "detail":
            text = "q:quit  Esc:back to job list  r:refresh"
        self._addstr(stdscr, y, 0, self._clip(text.ljust(max(0, width - 1)), width), curses.color_pair(PAIR_FOOTER))

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
