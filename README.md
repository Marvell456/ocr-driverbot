# Driver Payment Reviewer

A Python desktop app that reads timestamps from photos and calculates overtime pay automatically.

## Why I made this

My family employs a driver, and every night he sends a photo of the car parked after his shift. The timestamp is somewhere on the image, sometimes clear, sometimes not. At the end of the month, my mom has to go through all of them to figure out how much overtime he's owed. The rule is simple: after 6 PM, it's Rp 10,000 per hour. The problem is the process. She'd open each photo, zoom in, read the time, and keep a running total on paper. It took hours, and mistakes were easy to make when you're staring at tiny text on a phone screen.

After watching her do this a few times, I realized I could automate most of it. So I built this program. Point it at a folder of photos, and it handles the rest: reads the timestamps, applies the rules, and shows the total.

## What it does

- Scans a folder of images.
- Reads the timestamp from each photo using OCR.
- If OCR fails, it tries to get the time from the filename. Some phones save photos as `TimePhoto_20260901_183356.jpg`, which already contains the date and time.
- Calculates overtime based on the time:
  - Before 4 AM: counts as the previous day.
  - 4 AM to 6 PM: ignored, because it's not overtime.
  - After 6 PM: paid at Rp 10,000 per hour.
- Finds duplicate files using a hash of the file contents, so the same photo saved twice isn't counted twice.
- If two different photos have timestamps on the same shift date, it keeps the later one and flags the other.
- Shows a summary table, a running total, and a list of ignored files with the reason each one was ignored.
- Exports the summary and ignored files to Excel.

## How it works

The app uses Tesseract for OCR. Before reading the image, it preprocesses it: converts it to grayscale, increases contrast, thresholds it, and resizes it. Then it tries a few different crops of the image where the timestamp usually appears, along with multiple OCR settings, to improve the chance of a clean read.

Once it has a timestamp, it applies the payment rules. If the timestamp falls in the invalid window, the file is ignored. If it's after midnight but before 4 AM, the shift date is moved back one day, and overtime is counted from 6 PM the previous day.

To avoid redoing work, the app caches OCR results using the file's SHA-256 hash. If you run it again later, it only re-reads new or changed files.

The interface is built with Tkinter. Each kept photo gets its own tab with the image and an editable timestamp field, so OCR mistakes can be corrected if needed. The summary tab shows the final numbers.

## What I learned

This was the first project where I was building for someone other than myself. That changed things. When I write code for school, it either works or it doesn't. When I write code for my mom, it has to work on the messy photos: the timestamp cut off at the edge, or a digit Tesseract reads wrong. Every bug I found had a real consequence, because it would make her life harder.

The bigger surprise was how much of the work happened before the OCR even ran. Tesseract would fail on the raw photos more often than not, because the timestamp is printed in a thin font over a busy background. I ended up spending more time cropping, resizing, and thresholding the image than writing the payment logic. That was the part I didn't expect. Making the input readable turned out to be harder than calculating the output.

I also had to think about who would actually use this. My mom doesn't want to open a terminal or read a log file. She wants to click a button and see a number. That constraint shaped the whole interface, and it's the part of the project I'm most proud of.

## Built with

- Python
- Tkinter for the GUI
- Pillow for image processing
- pytesseract and Tesseract for OCR
- pandas and openpyxl for Excel export

## Requirements

- Python 3
- Tesseract OCR installed
- Python packages: Pillow, pytesseract, pandas, openpyxl

## Usage

1. Install Tesseract OCR and set the path to the executable in the script.
2. Install the Python dependencies:
   ```
   pip install pillow pytesseract pandas openpyxl
   ```
3. Put your photos in a folder named `data`.
4. Run the script.

## Notes

- Only `.png`, `.jpg`, and `.jpeg` files are scanned.
- The cache is stored in `data/.ocr_cache.json`.
- The overtime rate and time rules are defined at the top of the script and can be changed if needed.
