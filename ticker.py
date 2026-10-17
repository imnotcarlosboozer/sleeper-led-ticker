#!/usr/bin/env python3
"""Scroll Sleeper fantasy football injury news across HUB75 RGB LED panels.

Run on the Pi:     sudo python3 ticker.py
Preview anywhere:  python3 ticker.py --preview
"""
import argparse
import json
import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DATA_PATH = os.path.join(APP_DIR, "injuries.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")
FETCH_SCRIPT = os.path.join(APP_DIR, "sleeper_fetch.py")
FONT_DIR = os.path.join(APP_DIR, "fonts")

RETRY_AFTER_FAILURE_SECS = 15 * 60

WHITE = (255, 255, 255)
GRAY = (150, 150, 150)
TEAL = (0, 180, 200)
DIM = (60, 60, 60)
GREEN = (0, 220, 0)
RED = (255, 30, 30)
AXIS = (110, 110, 110)
STATUS_COLORS = {
    None: GREEN,  # healthy again
    "Questionable": (255, 220, 0),
    "Doubtful": (255, 130, 0),
}
DEFAULT_STATUS_COLOR = RED  # Out, IR, PUP, Sus, etc.

SEPARATOR = "   *   "

# Pixel-art stock chart "emoji". '#' = axis, 'L' = trend line.
ICON_ROWS = {
    "up": [
        "#............",
        "#........LLLL",
        "#..........LL",
        "#.........L.L",
        "#........L..L",
        "#...L...L....",
        "#..L.L.L.....",
        "#.L...L......",
        "#L...........",
        "#............",
        "#############",
    ],
    "down": [
        "#............",
        "#L...........",
        "#.L...L......",
        "#..L.L.L.....",
        "#...L...L....",
        "#........L..L",
        "#.........L.L",
        "#..........LL",
        "#........LLLL",
        "#............",
        "#############",
    ],
}
ICON_LINE_COLORS = {"up": GREEN, "down": RED}
ICON_WIDTH = len(ICON_ROWS["up"][0])
ICON_HEIGHT = len(ICON_ROWS["up"])
ICON_GAP = 3
ICON_EMOJI = {"up": "\U0001F4C8", "down": "\U0001F4C9"}  # for --preview


def icon_pixels(direction):
    """List of (dx, dy, rgb) for one icon."""
    line = ICON_LINE_COLORS[direction]
    return [
        (dx, dy, AXIS if ch == "#" else line)
        for dy, row in enumerate(ICON_ROWS[direction])
        for dx, ch in enumerate(row)
        if ch != "."
    ]


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_data(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status_label(status):
    return status.upper() if status else "ACTIVE"


def status_color(status):
    return STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR)


def build_segments(data):
    """Return the ticker as a list of runs: (text, rgb) or (None, "up"/"down") for an icon."""
    if data is None:
        return [("LOADING SLEEPER INJURY REPORT...", WHITE)]
    items = data.get("items") or []
    if not items:
        return [("NO INJURIES REPORTED", WHITE)]

    segs = []
    for item in items:
        trend = item.get("trend")
        if trend:
            segs.append((None, trend["direction"]))
        segs.append((item["name"].upper(), WHITE))
        segs.append((f" {item['position']} {item['team']} ", GRAY))
        if trend:
            segs.append((status_label(trend["from"]), status_color(trend["from"])))
            segs.append((" > ", GRAY))
        segs.append((status_label(item["status"]), status_color(item["status"])))
        if item.get("detail"):
            segs.append((f" ({item['detail']})", GRAY))
        segs.append((SEPARATOR, TEAL))
    return segs


def header_text(data, fits):
    week = data.get("week") if data else None
    options = [f"WK{week} INJURIES", f"WK{week} INJ"] if week else []
    options += ["INJURIES", "INJ"]
    for text in options:
        if fits(text):
            return text
    return ""


class Fetcher:
    """Runs sleeper_fetch.py in a child process when the cached data is stale.

    A separate process keeps the ~15 MB JSON parse from stalling the scroll.
    """

    def __init__(self, refresh_hours):
        self.refresh_secs = refresh_hours * 3600
        self.proc = None
        self.last_failure = 0.0

    def is_stale(self):
        if not os.path.exists(HISTORY_PATH):
            return True  # start the trend baseline right away
        try:
            age = time.time() - os.path.getmtime(DATA_PATH)
        except OSError:
            return True
        return age > self.refresh_secs

    def poll(self):
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                return
            if code != 0:
                self.last_failure = time.time()
            self.proc = None
        if self.is_stale() and time.time() - self.last_failure > RETRY_AFTER_FAILURE_SECS:
            self.proc = subprocess.Popen(
                [sys.executable, FETCH_SCRIPT],
                preexec_fn=lambda: os.nice(10),
            )


def run_matrix(cfg, data_path):
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

    p = cfg["panel"]
    d = cfg["display"]
    opts = RGBMatrixOptions()
    opts.rows = p["rows"]
    opts.cols = p["cols"]
    opts.chain_length = p["chain_length"]
    opts.parallel = p["parallel"]
    opts.hardware_mapping = p["hardware_mapping"]
    opts.gpio_slowdown = p["gpio_slowdown"]
    opts.brightness = p["brightness"]
    opts.pwm_bits = p["pwm_bits"]
    # Keep root so the fetch child can write injuries.json next to this script.
    opts.drop_privileges = False

    matrix = RGBMatrix(options=opts)
    canvas = matrix.CreateFrameCanvas()
    width, height = canvas.width, canvas.height

    scroll_font = graphics.Font()
    scroll_font.LoadFont(os.path.join(FONT_DIR, d["scroll_font"]))
    header_font = graphics.Font()
    header_font.LoadFont(os.path.join(FONT_DIR, d["header_font"]))

    colors = {}

    def color(rgb):
        if rgb not in colors:
            colors[rgb] = graphics.Color(*rgb)
        return colors[rgb]

    def text_width(font, text):
        return sum(font.CharacterWidth(ord(c)) for c in text)

    icons = {direction: icon_pixels(direction) for direction in ICON_ROWS}

    def draw_icon(left, top, direction):
        for dx, dy, rgb in icons[direction]:
            x = left + dx
            if 0 <= x < width:
                canvas.SetPixel(x, top + dy, *rgb)

    # Show a header above the scroller only if there's room for both.
    show_header = d["show_header"] and height >= header_font.height + 2 + scroll_font.height
    if show_header:
        header_y = header_font.baseline
        rule_y = header_font.height + 1
        region_top = rule_y + 1
    else:
        region_top = 0
    scroll_y = region_top + (height - region_top - scroll_font.height) // 2 + scroll_font.baseline
    # Icon's bottom axis lines up with the bottom of the capital letters.
    icon_top = scroll_y - ICON_HEIGHT

    fetcher = Fetcher(cfg["data"]["refresh_hours"])
    frame_secs = 1.0 / d["scroll_pixels_per_second"]
    data_mtime = None
    data = None

    while True:
        # Reload data only between passes so the text never jumps mid-scroll.
        try:
            mtime = os.path.getmtime(data_path)
        except OSError:
            mtime = None
        if mtime != data_mtime:
            data, data_mtime = load_data(data_path), mtime

        runs = []
        x = 0
        for text, value in build_segments(data):
            if text is None:
                w = ICON_WIDTH + ICON_GAP
                runs.append((x, w, None, value))
            else:
                w = text_width(scroll_font, text)
                runs.append((x, w, text, color(value)))
            x += w
        total_width = x

        header = header_text(data, lambda t: text_width(header_font, t) <= width) if show_header else ""
        header_x = (width - text_width(header_font, header)) // 2

        pos = width
        next_frame = time.monotonic()
        while pos + total_width > 0:
            canvas.Clear()
            if show_header:
                graphics.DrawText(canvas, header_font, header_x, header_y, color(TEAL), header)
                graphics.DrawLine(canvas, 0, rule_y, width - 1, rule_y, color(DIM))
            for rx, rw, text, value in runs:
                left = pos + rx
                if left >= width:
                    break
                if left + rw <= 0:
                    continue
                if text is None:
                    draw_icon(left, icon_top, value)
                else:
                    graphics.DrawText(canvas, scroll_font, left, scroll_y, value, text)
            canvas = matrix.SwapOnVSync(canvas)

            pos -= 1
            if pos % 64 == 0:
                fetcher.poll()
            next_frame += frame_secs
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()
        fetcher.poll()


def run_preview(cfg, data_path):
    """Print the ticker text with terminal colors, for testing without a Pi."""
    data = load_data(data_path)
    if data is None:
        print("No data yet; fetching...")
        subprocess.run([sys.executable, FETCH_SCRIPT], check=True)
        data = load_data(DATA_PATH)

    def ansi(text, rgb):
        return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"

    segs = build_segments(data)
    glyph_w = int(cfg["display"]["scroll_font"].split("x")[0])
    panel_w = cfg["panel"]["cols"] * cfg["panel"]["chain_length"]
    total_px = panel_w + sum(
        ICON_WIDTH + ICON_GAP if t is None else len(t) * glyph_w for t, _ in segs
    )
    secs = total_px / cfg["display"]["scroll_pixels_per_second"]

    print(ansi(header_text(data, lambda t: len(t) * 5 <= panel_w), TEAL))
    print("".join(ICON_EMOJI[v] + " " if t is None else ansi(t, v) for t, v in segs))
    print(f"\n{len(data.get('items', []))} players, ~{total_px} px wide, one pass takes ~{secs / 60:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="print ticker text instead of driving the panel")
    parser.add_argument("--data", default=DATA_PATH, help="show a different injuries file (e.g. demo_injuries.json)")
    args = parser.parse_args()
    cfg = load_config()
    if args.preview:
        run_preview(cfg, args.data)
    else:
        try:
            run_matrix(cfg, args.data)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
