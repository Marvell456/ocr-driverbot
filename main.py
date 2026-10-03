DATA_DIR = "data"
TESSERACT_CMD = r"C:\Users\MSI\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
VALID_EXTS = {".png", ".jpg", ".jpeg"}
RATE_PER_HOUR = 10_000
OT_START_HOUR = 18
AFTER_MIDNIGHT_CUTOFF_HOUR = 4
INVALID_WINDOW_START_HOUR = 4
INVALID_WINDOW_END_HOUR = 18
CACHE_VERSION = 3
TARGET_WIDTH = 1100
UPSCALE_MIN_WIDTH = 600

import json
import hashlib
import os
import queue
import re
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import pandas as pd
import pytesseract
from PIL import Image, ImageOps, ImageTk

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
CACHE_FILE = os.path.join(DATA_DIR, ".ocr_cache.json")

MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
TIMESTAMP_RE = re.compile(
    r"(?<!\d)(\d{1,2})[\s.,;_-]*([A-Za-z]{3})[\s.,;_:-]*(\d{4})"
    r"[\s.,;:|%*()_-]*(\d{1,2})[\s.,;:|%*()_-]*(\d{1,2})"
    r"[\s.,;:|%*()_-]*(\d{1,2})(?!\d)"
)


# OCR parsing
def parse_timestamp(text):
    text = re.sub(
        r"(\d{4})\s*[*%][.,:)]*\s*([89][.,:]\d{1,2}[.,:]\d{1,2})(?!\d)",
        r"\g<1>1\2",
        text or "",
    )
    for match in TIMESTAMP_RE.finditer(text):
        day, month_text, year, hour, minute, second = match.groups()
        month = MONTHS.get(month_text.title())
        if month is None:
            continue
        try:
            return datetime(
                int(year), month, int(day), int(hour), int(minute), int(second)
            )
        except ValueError:
            continue
    return None


def _prepare(crop, threshold=160, binarize=True):
    gray = ImageOps.autocontrast(crop.convert("L"))
    if binarize:
        gray = gray.point(lambda pixel: 0 if pixel > threshold else 255).convert("L")
    if gray.width > TARGET_WIDTH:
        ratio = TARGET_WIDTH / gray.width
        gray = gray.resize(
            (TARGET_WIDTH, max(1, round(gray.height * ratio))), Image.Resampling.BILINEAR
        )
    elif gray.width < UPSCALE_MIN_WIDTH:
        ratio = UPSCALE_MIN_WIDTH / gray.width
        gray = gray.resize(
            (UPSCALE_MIN_WIDTH, max(1, round(gray.height * ratio))), Image.Resampling.BILINEAR
        )
    return gray


def _ocr_variants(image):
    width, height = image.size
    regions = [
        (image.crop((int(width * 0.30), int(height * 0.70), width, int(height * 0.82))), True, 6),
        (image.crop((int(width * 0.30), int(height * 0.70), width, int(height * 0.82))), False, 6),
        (image.crop((int(width * 0.30), int(height * 0.72), width, int(height * 0.82))), False, 6),
        (image.crop((int(width * 0.30), int(height * 0.68), width, height)), True, 6),
        (image.crop((int(width * 0.30), int(height * 0.68), width, height)), False, 6),
        (image.crop((0, int(height * 0.55), width, height)), True, 6),
    ]
    for crop, binarize, psm in regions:
        yield pytesseract.image_to_string(
            _prepare(crop, binarize=binarize), config=f"--psm {psm}"
        )


def _timestamp_from_filename(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    match = re.fullmatch(r"TimePhoto_(\d{8})_(\d{6})", stem)
    if match is None:
        return None
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def ocr_image(path):
    filename_timestamp = _timestamp_from_filename(path)
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as exc:
        print(f"OCR failed for {os.path.basename(path)}: {exc}")
        return ""

    for text in _ocr_variants(image):
        timestamp = parse_timestamp(text)
        if timestamp is None:
            continue
        if filename_timestamp is not None:
            difference = abs((timestamp - filename_timestamp).total_seconds())
            if difference > 120:
                timestamp = filename_timestamp
        return timestamp.strftime("%d %b %Y %H.%M.%S")

    if filename_timestamp is not None:
        return filename_timestamp.strftime("%d %b %Y %H.%M.%S")
    return ""


# Payment rules
def shift_date(ts):
    if ts is None:
        return None
    if ts.hour < AFTER_MIDNIGHT_CUTOFF_HOUR:
        return (ts - timedelta(days=1)).date()
    return ts.date()


def overtime_minutes(ts):
    if ts is None:
        return 0.0
    if INVALID_WINDOW_START_HOUR <= ts.hour < INVALID_WINDOW_END_HOUR:
        return None
    if ts.hour < AFTER_MIDNIGHT_CUTOFF_HOUR:
        return 6 * 60 + ts.hour * 60 + ts.minute + ts.second / 60
    if ts.hour == OT_START_HOUR and ts.minute == 0 and ts.second == 0:
        return 0.0
    elapsed_seconds = (
        (ts.hour - OT_START_HOUR) * 3600 + ts.minute * 60 + ts.second
    )
    return elapsed_seconds / 60


def payment(ts):
    minutes = overtime_minutes(ts)
    if minutes is None:
        return None
    return minutes / 60 * RATE_PER_HOUR


def time_text(ts):
    return ts.strftime("%H:%M:%S") if ts is not None else "Unparsed"


def invalid_time_reason(ts):
    return f"time {time_text(ts)} falls in 04:00–18:00"


def collect_filenames():
    if not os.path.isfile(TESSERACT_CMD):
        return None, ("Tesseract not found", TESSERACT_CMD)
    if not os.path.isdir(DATA_DIR):
        return None, ("Data folder missing", f"The folder '{DATA_DIR}' was not found.")

    filenames = sorted(
        (
            name
            for name in os.listdir(DATA_DIR)
            if os.path.splitext(name)[1].lower() in VALID_EXTS
            and os.path.isfile(os.path.join(DATA_DIR, name))
        ),
        key=str.casefold,
    )
    if not filenames:
        return None, ("No images found", f"No valid images were found in '{DATA_DIR}'.")
    return filenames, None


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as image_file:
        for chunk in iter(lambda: image_file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (OSError, ValueError):
        return {}
    if not isinstance(cache, dict) or cache.get("_version") != CACHE_VERSION:
        return {}
    return {key: value for key, value in cache.items() if key != "_version"}


def _save_cache(cache, live_keys):
    payload = {"_version": CACHE_VERSION}
    payload.update({key: cache[key] for key in live_keys if key in cache})
    temporary_path = CACHE_FILE + ".tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as cache_file:
            json.dump(payload, cache_file)
        os.replace(temporary_path, CACHE_FILE)
    except OSError as exc:
        print(f"Could not write OCR cache: {exc}")


def _record(filename, digest, text):
    ts = parse_timestamp(text)
    return {
        "file": filename,
        "path": os.path.join(DATA_DIR, filename),
        "text": text,
        "ts": ts,
        "shift_date": shift_date(ts),
        "overtime_minutes": overtime_minutes(ts),
        "pay": payment(ts),
        "sha256": digest,
    }


def _stub_record(filename, digest, primary):
    return {
        "file": filename,
        "path": os.path.join(DATA_DIR, filename),
        "text": primary["text"],
        "ts": primary["ts"],
        "shift_date": primary["shift_date"],
        "overtime_minutes": primary["overtime_minutes"],
        "pay": primary["pay"],
        "sha256": digest,
    }


def scan(filenames, progress=None, max_workers=None):
    cache = _load_cache()
    groups = {}
    unreadable = []
    for filename in filenames:
        path = os.path.join(DATA_DIR, filename)
        try:
            digest = _file_sha256(path)
        except OSError as exc:
            print(f"Could not hash {filename}: {exc}")
            unreadable.append(filename)
            continue
        groups.setdefault(digest, []).append(filename)

    targets = [
        (members[0], digest)
        for digest, members in groups.items()
    ]
    targets.extend((filename, None) for filename in unreadable)
    targets.sort(key=lambda item: item[0].casefold())

    texts = {}
    pending = []
    done = 0
    total = len(targets)

    def report(filename):
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total, filename)

    for filename, digest in targets:
        key = digest if digest is not None else ("unreadable", filename)
        if digest is not None and digest in cache:
            texts[key] = cache[digest]
            report(filename)
        else:
            pending.append((filename, digest, key))

    if max_workers is None:
        max_workers = min(4, os.cpu_count() or 1)
    if len(pending) == 1 or max_workers <= 1:
        for filename, _digest, key in pending:
            texts[key] = ocr_image(os.path.join(DATA_DIR, filename))
            report(filename)
    elif pending:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as executor:
            futures = {
                executor.submit(ocr_image, os.path.join(DATA_DIR, filename)): (
                    filename,
                    key,
                )
                for filename, _digest, key in pending
            }
            for future in as_completed(futures):
                filename, key = futures[future]
                texts[key] = future.result()
                report(filename)

    records_by_digest = {}
    unreadable_records = []
    for filename, digest in targets:
        key = digest if digest is not None else ("unreadable", filename)
        record = _record(filename, digest, texts[key])
        if digest is None:
            unreadable_records.append(record)
        else:
            records_by_digest[digest] = record
            cache[digest] = texts[key]
    _save_cache(cache, set(groups))

    ignored_by_time = []
    dropped = []
    survivors = []
    for digest, members in groups.items():
        record = records_by_digest[digest]
        if record["pay"] is None:
            reason = invalid_time_reason(record["ts"])
            for filename in members:
                ignored_by_time.append(
                    {
                        "record": _stub_record(filename, digest, record),
                        "reason": reason,
                    }
                )
        else:
            survivors.append(record)
            for filename in members[1:]:
                dropped.append(
                    {
                        "record": _stub_record(filename, digest, record),
                        "reason": "exact-file",
                        "kept_file": record["file"],
                        "type": "duplicate",
                    }
                )

    for record in unreadable_records:
        if record["pay"] is None:
            ignored_by_time.append(
                {"record": record, "reason": invalid_time_reason(record["ts"])}
            )
        else:
            survivors.append(record)

    by_shift_date = {}
    for record in survivors:
        date = record["shift_date"]
        if date is not None:
            by_shift_date.setdefault(date, []).append(record)

    conflicted_out = set()
    for shift, group in by_shift_date.items():
        if len(group) <= 1:
            continue
        kept = max(group, key=lambda item: item["ts"])
        for record in group:
            if record is kept:
                continue
            conflicted_out.add(id(record))
            dropped.append(
                {
                    "record": record,
                    "reason": "same-day-conflict",
                    "kept_file": kept["file"],
                    "type": "same-day",
                }
            )

    kept_records = [record for record in survivors if id(record) not in conflicted_out]
    dropped.sort(key=lambda item: item["record"]["file"].casefold())
    ignored_by_time.sort(key=lambda item: item["record"]["file"].casefold())
    return kept_records, dropped, ignored_by_time


# GUI helpers
def sorted_records(records):
    return sorted(
        records,
        key=lambda item: (
            item["shift_date"] is None,
            item["shift_date"] or datetime.max.date(),
            item["ts"] or datetime.max,
            item["file"].casefold(),
        ),
    )


def format_money(amount):
    return "" if amount is None else f"Rp {amount:,.2f}".removesuffix(".00")


class DriverPaymentApp:
    def __init__(self, root, kept, dropped, ignored_by_time):
        self.root = root
        self.kept = kept
        self.dropped = dropped
        self.ignored_by_time = ignored_by_time
        self.summary_tree = None
        self.total_label = None
        self.image_references = []

        root.title("Driver Payment Reviewer")
        root.geometry("1250x820")
        root.minsize(900, 600)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        for record in self.kept:
            self._add_record_tab(record)
        self._add_summary_tab()
        self.refresh_summary()

    def _add_record_tab(self, record):
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text=record["file"])
        tab.columnconfigure(0, weight=1, minsize=400)
        tab.columnconfigure(1, weight=1, minsize=360)
        tab.rowconfigure(0, weight=1)

        image_frame = ttk.Frame(tab, width=560, height=720)
        image_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        image_frame.grid_propagate(False)
        image_label = ttk.Label(image_frame, anchor="center")
        image_label.pack(fill="both", expand=True)
        try:
            with Image.open(record["path"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((560, 720), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            image_label.configure(image=photo)
            self.image_references.append(photo)
        except Exception as exc:
            image_label.configure(text=f"Unable to display image\n{exc}", wraplength=480)

        editor = ttk.Frame(tab)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        timestamp_entry = ttk.Entry(editor, width=32, font=("TkFixedFont", 11))
        timestamp_entry.grid(row=0, column=0, sticky="ew")
        timestamp_entry.insert(0, record["text"])
        record["timestamp_entry"] = timestamp_entry
        record["info_label"] = ttk.Label(editor, anchor="w", justify="left")
        record["info_label"].grid(row=1, column=0, sticky="ew", pady=(8, 4))
        ttk.Button(
            editor,
            text="Recalculate",
            command=lambda current=record: self.recalculate(current),
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._update_info(record)

    def _update_info(self, record):
        date_text = record["shift_date"].isoformat() if record["shift_date"] else "Unparsed"
        if record["pay"] is None:
            pay_text = "Ignored by time (04:00–18:00)"
        else:
            pay_text = format_money(record["pay"])
        hours = (
            f"{record['overtime_minutes'] / 60:.2f} h"
            if record["overtime_minutes"] is not None
            else "Ignored"
        )
        record["info_label"].configure(
            text=(
                f"Shift date: {date_text}    Time: {time_text(record['ts'])}    "
                f"Overtime: {hours}    Pay: {pay_text}"
            )
        )

    def recalculate(self, record):
        text = record["timestamp_entry"].get().strip()
        ts = parse_timestamp(text)
        if ts is not None:
            text = ts.strftime("%d %b %Y %H.%M.%S")
            record["timestamp_entry"].delete(0, tk.END)
            record["timestamp_entry"].insert(0, text)
        minutes = overtime_minutes(ts)
        record.update(
            {
                "text": text,
                "ts": ts,
                "shift_date": shift_date(ts),
                "overtime_minutes": minutes,
                "pay": payment(ts),
            }
        )
        self._update_info(record)
        self.refresh_summary()

    def _add_summary_tab(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Summary")
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)

        columns = ("file", "shift_date", "time", "overtime", "pay")
        self.summary_tree = ttk.Treeview(tab, columns=columns, show="headings", height=18)
        headings = {
            "file": "File",
            "shift_date": "Shift Date",
            "time": "Time",
            "overtime": "Overtime (h)",
            "pay": "Pay (Rp)",
        }
        widths = {"file": 230, "shift_date": 110, "time": 100, "overtime": 110, "pay": 140}
        for column in columns:
            self.summary_tree.heading(column, text=headings[column])
            self.summary_tree.column(column, width=widths[column], anchor="w")
        self.summary_tree.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        tree_scroll = ttk.Scrollbar(tab, orient="vertical", command=self.summary_tree.yview)
        tree_scroll.grid(row=0, column=0, sticky="nse")
        self.summary_tree.configure(yscrollcommand=tree_scroll.set)

        side = ttk.Frame(tab)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        ttk.Label(side, text="Ignored and dropped files").grid(row=0, column=0, sticky="w")
        self.ignored_list = tk.Listbox(side, height=20, width=55)
        self.ignored_list.grid(row=1, column=0, sticky="nsew", pady=(6, 12))
        side.rowconfigure(1, weight=1)
        self.total_label = ttk.Label(side, text="Total: Rp 0", font=("TkDefaultFont", 11, "bold"))
        self.total_label.grid(row=2, column=0, sticky="w", pady=(0, 10))
        buttons = ttk.Frame(side)
        buttons.grid(row=3, column=0, sticky="w")
        ttk.Button(buttons, text="Refresh totals", command=self.refresh_summary).pack(side="left")
        ttk.Button(buttons, text="Export to Excel", command=self.export_excel).pack(
            side="left", padx=(8, 0)
        )

    def refresh_summary(self):
        for row_id in self.summary_tree.get_children():
            self.summary_tree.delete(row_id)
        total = 0.0
        for record in sorted_records(self.kept):
            minutes = record["overtime_minutes"]
            pay = record["pay"]
            if pay is not None:
                total += pay
            self.summary_tree.insert(
                "",
                "end",
                values=(
                    record["file"],
                    record["shift_date"].isoformat() if record["shift_date"] else "",
                    time_text(record["ts"]),
                    f"{minutes / 60:.2f}" if minutes is not None else "",
                    format_money(pay) if pay is not None else "Ignored",
                ),
            )
        self.total_label.configure(text=f"Total: {format_money(total)}")

        self.ignored_list.delete(0, tk.END)
        for item in self.dropped:
            self.ignored_list.insert(
                tk.END,
                f"{item['record']['file']} → {item['reason']} → kept: {item['kept_file']}",
            )
        for item in self.ignored_by_time:
            self.ignored_list.insert(
                tk.END, f"{item['record']['file']} → {item['reason']}"
            )

    def export_excel(self):
        destination = filedialog.asksaveasfilename(
            title="Export payment summary",
            defaultextension=".xlsx",
            initialfile="summary.xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if not destination:
            return

        summary_rows = []
        for record in sorted_records(self.kept):
            summary_rows.append(
                {
                    "file": record["file"],
                    "shift_date": (
                        record["shift_date"].isoformat() if record["shift_date"] else ""
                    ),
                    "time": time_text(record["ts"]),
                    "overtime_hours": (
                        record["overtime_minutes"] / 60
                        if record["overtime_minutes"] is not None
                        else 0.0
                    ),
                    "pay_rp": record["pay"] if record["pay"] is not None else "",
                }
            )
        ignored_rows = [
            {
                "file": item["record"]["file"],
                "reason": item["reason"],
                "kept_file": item["kept_file"],
                "type": item["type"],
            }
            for item in self.dropped
        ]
        ignored_rows.extend(
            {
                "file": item["record"]["file"],
                "reason": item["reason"],
                "kept_file": "",
                "type": "ignored-time",
            }
            for item in self.ignored_by_time
        )
        try:
            with pd.ExcelWriter(destination, engine="openpyxl") as writer:
                pd.DataFrame(
                    summary_rows,
                    columns=["file", "shift_date", "time", "overtime_hours", "pay_rp"],
                ).to_excel(writer, sheet_name="Summary", index=False)
                pd.DataFrame(
                    ignored_rows, columns=["file", "reason", "kept_file", "type"]
                ).to_excel(writer, sheet_name="Ignored", index=False)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        messagebox.showinfo("Export complete", f"Saved to {destination}", parent=self.root)


def show_startup_notice(root, dropped, ignored_by_time):
    if not dropped and not ignored_by_time:
        messagebox.showinfo("Scan complete", "No duplicates or ignored files found.", parent=root)
        return

    duplicate_lines = ["Duplicates / same-day conflicts"]
    duplicate_lines.extend(
        f"{item['record']['file']} → ignored ({item['reason']}) → kept: {item['kept_file']}"
        for item in dropped
    )
    if not dropped:
        duplicate_lines.append("None")

    time_lines = ["Ignored by time"]
    time_lines.extend(
        f"{item['record']['file']} → {item['reason']}" for item in ignored_by_time
    )
    if not ignored_by_time:
        time_lines.append("None")

    messagebox.showinfo(
        "Scan results",
        "\n".join(duplicate_lines) + "\n\n" + "\n".join(time_lines),
        parent=root,
    )


def main():
    root = tk.Tk()
    root.title("Driver Payment Reviewer")
    filenames, error = collect_filenames()
    if error is not None:
        root.withdraw()
        if error[0] == "Tesseract not found":
            messagebox.showerror(error[0], error[1], parent=root)
        else:
            messagebox.showwarning(error[0], error[1], parent=root)
        root.destroy()
        return

    root.geometry("560x170")
    progress_frame = ttk.Frame(root, padding=24)
    progress_frame.pack(fill="both", expand=True)
    ttk.Label(
        progress_frame,
        text="Scanning images...",
        font=("TkDefaultFont", 12, "bold"),
    ).pack(anchor="w")
    status = ttk.Label(progress_frame, text=f"0 / {len(filenames)}")
    status.pack(anchor="w", pady=(10, 4))
    progress_bar = ttk.Progressbar(
        progress_frame, mode="determinate", maximum=len(filenames), length=460
    )
    progress_bar.pack(fill="x")

    messages = queue.Queue()

    def on_progress(done, total, filename):
        messages.put(("progress", done, total, filename))

    def worker():
        try:
            messages.put(("done", scan(filenames, progress=on_progress)))
        except Exception as exc:
            messages.put(("error", exc))

    def poll_messages():
        try:
            while True:
                message = messages.get_nowait()
                if message[0] == "progress":
                    _, done, total, filename = message
                    progress_bar["maximum"] = total
                    progress_bar["value"] = done
                    status.configure(text=f"{done} / {total}   {filename}")
                elif message[0] == "done":
                    _, (kept, dropped, ignored_by_time) = message
                    progress_frame.destroy()
                    app = DriverPaymentApp(root, kept, dropped, ignored_by_time)
                    root.driver_payment_app = app
                    show_startup_notice(root, dropped, ignored_by_time)
                    return
                else:
                    _, exc = message
                    messagebox.showerror("Scan failed", str(exc), parent=root)
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(80, poll_messages)

    threading.Thread(target=worker, daemon=True).start()
    root.after(80, poll_messages)
    root.mainloop()


if __name__ == "__main__":
    main()