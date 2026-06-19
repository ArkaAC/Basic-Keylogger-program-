"""
key_input_demo.py  —  Keyboard Input Within Your Own App
=========================================================
Uses Python's built-in `curses` library to demonstrate how to capture
and respond to keyboard input scoped entirely to THIS running process.

No system-wide hooks. No hidden listeners. The moment this program exits,
all keyboard capture stops completely.

Features
--------
  • Live "Last key pressed" display (name + raw code)
  • Timestamped key-press history panel
  • In-app text editor with word-wrap and newline support
  • Special key detection: arrows, F-keys, Ctrl combos, Tab, Escape
  • Ctrl+S  → save your typed text to  typed_output.txt
  • Ctrl+C  → clear the text buffer
  • Ctrl+Q  → quit cleanly

Run
---
  python key_input_demo.py

Requirements
------------
  Python 3.8+  (curses ships with the standard library on macOS / Linux)
  Windows      :  pip install windows-curses
"""

import curses
import time
from pathlib import Path

OUTPUT_FILE = Path("typed_output.txt")
MAX_HISTORY = 20       # lines kept in the key-history panel


# ── Key-name lookup ──────────────────────────────────────────────────────────

def key_label(code: int) -> str:
    """Return a human-readable string for any curses key code."""
    NAMED: dict[int, str] = {
        curses.KEY_UP:        "↑  Arrow Up",
        curses.KEY_DOWN:      "↓  Arrow Down",
        curses.KEY_LEFT:      "←  Arrow Left",
        curses.KEY_RIGHT:     "→  Arrow Right",
        curses.KEY_BACKSPACE: "⌫  Backspace",
        curses.KEY_DC:        "Delete",
        curses.KEY_IC:        "Insert",
        curses.KEY_HOME:      "Home",
        curses.KEY_END:       "End",
        curses.KEY_PPAGE:     "Page Up",
        curses.KEY_NPAGE:     "Page Down",
        curses.KEY_BTAB:      "⇧⇥  Shift+Tab",
        curses.KEY_F1:  "F1",   curses.KEY_F2:  "F2",
        curses.KEY_F3:  "F3",   curses.KEY_F4:  "F4",
        curses.KEY_F5:  "F5",   curses.KEY_F6:  "F6",
        curses.KEY_F7:  "F7",   curses.KEY_F8:  "F8",
        curses.KEY_F9:  "F9",   curses.KEY_F10: "F10",
        curses.KEY_F11: "F11",  curses.KEY_F12: "F12",
        9:   "⇥  Tab",
        10:  "↵  Enter",
        27:  "Escape",
        32:  "Space",
        127: "⌫  Backspace (DEL)",
    }
    if code in NAMED:
        return NAMED[code]
    if 1 <= code <= 26:         # Ctrl+A … Ctrl+Z  (ASCII 1–26)
        return f"Ctrl+{chr(code + 64)}"
    if 32 <= code <= 126:       # Printable ASCII
        return f"'{chr(code)}'"
    return f"<code {code}>"


# ── Safe drawing helpers ─────────────────────────────────────────────────────

def safe_add(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """addstr that silently ignores any out-of-bounds or resize errors."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w:
        return
    clip = w - x - 1
    if clip <= 0:
        return
    try:
        win.addstr(y, x, text[:clip], attr)
    except curses.error:
        pass


def draw_hline(win, y: int, label: str = "", attr: int = 0) -> None:
    """Full-width horizontal rule, optionally with a centred label."""
    _, w = win.getmaxyx()
    try:
        win.addstr(y, 0, "─" * (w - 1), attr)
        if label:
            lx = max(0, (w - len(label) - 4) // 2)
            win.addstr(y, lx, f"  {label}  ", attr | curses.A_BOLD)
    except curses.error:
        pass


# ── Word-wrap helper ─────────────────────────────────────────────────────────

def wrap_text(text: str, width: int) -> list[str]:
    """Split text into display lines no wider than `width`, honouring \\n."""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        while len(para) > width:
            lines.append(para[:width])
            para = para[width:]
        lines.append(para)
    return lines


# ── Main curses application ──────────────────────────────────────────────────

def run_app(stdscr: "curses._CursesWindow") -> None:
    # ── Colours ──────────────────────────────────────────────────────────────
    curses.start_color()
    curses.use_default_colors()          # -1 = terminal's default background
    curses.init_pair(1, curses.COLOR_BLACK,  curses.COLOR_CYAN)   # header bar
    curses.init_pair(2, curses.COLOR_CYAN,   -1)                  # accent
    curses.init_pair(3, curses.COLOR_YELLOW, -1)                  # labels / dividers
    curses.init_pair(4, curses.COLOR_GREEN,  -1)                  # success
    curses.init_pair(5, curses.COLOR_RED,    -1)                  # warning / escape
    curses.init_pair(6, curses.COLOR_WHITE,  -1)                  # body text

    HDR   = curses.color_pair(1) | curses.A_BOLD
    ACC   = curses.color_pair(2) | curses.A_BOLD
    LBL   = curses.color_pair(3)
    OK    = curses.color_pair(4) | curses.A_BOLD
    WARN  = curses.color_pair(5) | curses.A_BOLD
    BODY  = curses.color_pair(6)

    curses.curs_set(1)        # visible blinking cursor
    stdscr.keypad(True)       # enable curses.KEY_* constants for special keys
    stdscr.nodelay(False)     # blocking read (no busy-loop)

    # ── App state ─────────────────────────────────────────────────────────────
    text_buf : list[str] = []   # characters the user has typed in the editor
    key_hist : list[str] = []   # timestamped key press log
    last_lbl : str       = ""   # label of the most-recent key
    last_code: int       = 0    # raw curses code of the most-recent key
    key_count: int       = 0    # total keys pressed this session
    status   : str       = "Ctrl+S  save    Ctrl+C  clear    Ctrl+Q  quit"
    status_p : int       = curses.color_pair(2)

    while True:
        rows, cols = stdscr.getmaxyx()

        # Guard: terminal must be large enough to draw anything useful
        if rows < 14 or cols < 42:
            stdscr.erase()
            safe_add(stdscr, 0, 0,
                     "Terminal too small — please widen / taller the window.", WARN)
            stdscr.refresh()
            stdscr.getch()
            continue

        stdscr.erase()

        # ── Header ───────────────────────────────────────────────────────────
        title = "  ⌨   Keyboard Input Demo  —  keys captured inside this process only   "
        safe_add(stdscr, 0, 0, title.center(cols - 1), HDR)

        # ── Top panels (key-info left, history right) ─────────────────────────
        PANEL_H = 7
        lw = cols // 2
        rw = cols - lw

        # Left: last key pressed
        try:
            lp = stdscr.derwin(PANEL_H, lw, 1, 0)
            lp.box()
            safe_add(lp, 0, 2, " last key pressed ", LBL)

            if last_lbl:
                disp = last_lbl[:lw - 6]
                cx   = max(1, (lw - len(disp)) // 2)
                safe_add(lp, 2, cx, disp, ACC)
                safe_add(lp, 3, 2, f"raw code : {last_code}", BODY)
            else:
                safe_add(lp, 2, 2, "(waiting for input…)", BODY)

            safe_add(lp, 5, 2, f"session total : {key_count} keys", BODY)
        except curses.error:
            pass

        # Right: history log
        try:
            rp = stdscr.derwin(PANEL_H, rw, 1, lw)
            rp.box()
            safe_add(rp, 0, 2, " key history ", LBL)
            visible_hist = key_hist[-(PANEL_H - 2):]
            for i, entry in enumerate(visible_hist, 1):
                safe_add(rp, i, 2, entry[:rw - 4], BODY)
        except curses.error:
            pass

        # ── Divider ───────────────────────────────────────────────────────────
        div_row = 1 + PANEL_H
        stdscr.attron(LBL)
        draw_hline(stdscr, div_row, "type below — text stays inside this process", LBL)
        stdscr.attroff(LBL)

        # ── Text editor ───────────────────────────────────────────────────────
        ed_top = div_row + 1
        ed_h   = rows - ed_top - 2
        ed_w   = cols - 2
        inner_w = ed_w - 4    # usable width inside the box border + 1-char pad

        if ed_h > 2:
            try:
                ed = stdscr.derwin(ed_h, ed_w, ed_top, 1)
                ed.box()
                safe_add(ed, 0, 2, " editor ", LBL)

                lines    = wrap_text("".join(text_buf), inner_w)
                vis_lines = lines[-(ed_h - 2):]

                for i, ln in enumerate(vis_lines, 1):
                    if i < ed_h - 1:
                        safe_add(ed, i, 2, ln, BODY)

                # Position cursor at end of last visible line
                cur_row = min(len(vis_lines),      ed_h - 2)
                cur_col = min(len(vis_lines[-1]) + 2 if vis_lines else 2, ed_w - 2)
                try:
                    ed.move(cur_row, cur_col)
                except curses.error:
                    pass
            except curses.error:
                pass

        # ── Status bar ────────────────────────────────────────────────────────
        safe_add(stdscr, rows - 1, 0, status.ljust(cols - 1), status_p | curses.A_BOLD)

        stdscr.refresh()

        # ── Blocking key read ─────────────────────────────────────────────────
        # stdscr.getch() suspends here until the user presses a key.
        # curses translates escape sequences into KEY_* constants automatically
        # because we called stdscr.keypad(True) above.
        code = stdscr.getch()

        # Update shared state
        label     = key_label(code)
        last_lbl  = label
        last_code = code
        key_count += 1
        ts = time.strftime("%H:%M:%S")
        key_hist.append(f"{ts}  {label}")
        if len(key_hist) > MAX_HISTORY:
            key_hist.pop(0)

        status_p = curses.color_pair(2)   # default status colour = accent

        # ── Key dispatch ──────────────────────────────────────────────────────

        if code == 17:                              # Ctrl+Q  →  quit
            break

        elif code == 19:                            # Ctrl+S  →  save buffer
            content = "".join(text_buf)
            OUTPUT_FILE.write_text(content, encoding="utf-8")
            status   = f"✓  {len(text_buf)} chars saved  →  {OUTPUT_FILE.resolve()}"
            status_p = curses.color_pair(4)

        elif code == 3:                             # Ctrl+C  →  clear buffer
            text_buf.clear()
            status   = "Buffer cleared.  Ctrl+S  save    Ctrl+Q  quit"
            status_p = curses.color_pair(5)

        elif code in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
            if text_buf:
                text_buf.pop()
            status = "Ctrl+S  save    Ctrl+C  clear    Ctrl+Q  quit"

        elif code == 10:                            # Enter  →  newline in buffer
            text_buf.append("\n")
            status = "Ctrl+S  save    Ctrl+C  clear    Ctrl+Q  quit"

        elif 32 <= code <= 126:                     # Printable ASCII character
            text_buf.append(chr(code))
            status = "Ctrl+S  save    Ctrl+C  clear    Ctrl+Q  quit"

        elif code in (curses.KEY_UP, curses.KEY_DOWN,
                      curses.KEY_LEFT, curses.KEY_RIGHT):
            status   = f"Navigation key:  {label}"
            status_p = curses.color_pair(3)

        elif code == 9:                             # Tab
            text_buf.append("    ")                # insert 4 spaces
            status   = "Tab → inserted 4 spaces."
            status_p = curses.color_pair(3)

        elif code == 27:                            # Escape
            status   = "Escape pressed.  Use Ctrl+Q to quit."
            status_p = curses.color_pair(5)

        else:                                       # Any other special key
            status   = f"Special key:  {label}  (raw code {code})"
            status_p = curses.color_pair(3)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    print("┌──────────────────────────────────────────────────┐")
    print("│        Keyboard Input Demo  (curses)             │")
    print("├──────────────────────────────────────────────────┤")
    print("│  Keys are captured ONLY while this app is open.  │")
    print("│  No system-wide hooks. Nothing runs in the bg.   │")
    print("│                                                   │")
    print("│  Controls inside the app:                        │")
    print("│    Ctrl+S  →  save typed text to a file          │")
    print("│    Ctrl+C  →  clear the text buffer              │")
    print("│    Ctrl+Q  →  quit                               │")
    print("└──────────────────────────────────────────────────┘")
    print("\nPress Enter to launch…", end="", flush=True)
    input()

    # curses.wrapper() sets up the terminal, runs our function,
    # then ALWAYS restores the terminal on exit (even if an exception occurs).
    curses.wrapper(run_app)

    print("\n✓  Session ended. Terminal restored.")
    if OUTPUT_FILE.exists():
        print(f"   Saved text lives at: {OUTPUT_FILE.resolve()}")
    print()


if __name__ == "__main__":
    main()
