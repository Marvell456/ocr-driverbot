# Driver Payment Reviewer

## Why I made this

My mom employs a driver. Every night, the driver sends a photo of the car parked, with the time, date, and location printed somewhere on the image. At the end of the month, all of those photos have to be reviewed to figure out how much overtime to pay him.

The rule is simple: if he gets home after 6 PM, he gets Rp 10,000 per hour of overtime. But doing it by hand meant zooming into each photo, reading the timestamp, and keeping a running total on paper. It took a long time, and it was easy to lose track or make a mistake.

I built this program so the process can be done automatically from a folder of photos.

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

The app uses Tesseract for OCR. Before reading the image, it preprocesses it — converts it to grayscale, increases contrast, thresholds it, and resizes it — then tries a few different crops of the image where the timestamp usually appears. It also tries multiple OCR settings to improve the chance of a clean read.

Once it has a timestamp, it applies the payment rules. If the timestamp falls in the invalid window, the file is ignored. If it's after midnight but before 4 AM, the shift date is moved back one day, and overtime is counted from 6 PM the previous day.

To avoid redoing work, the app caches OCR results using the file's SHA-256 hash. If you run it again later, it only re-reads new or changed files.

The interface is built with Tkinter. Each kept photo gets its own tab with the image and an editable timestamp field, so OCR mistakes can be corrected if needed. The summary tab shows the final numbers.

## What I learned

This was one of the first times I built something for a real person instead of just for a grade. That made me think differently about the code. It wasn't enough for it to work on my computer with my test images. It had to handle blurry photos, missing timestamps, duplicate files, and times that didn't fit the normal pattern.

I also learned that OCR is not perfect. A lot of the work was not in reading the text, but in making the image easier to read. And I learned that a small program can save someone a lot of time if it fits into their actual routine.

## Built with

- Python
- Tkinter for the GUI
- Pillow for image processing
- pytesseract and Tesseract for OCR
- pandas and openpyxl for Excel export

## Running it

1. Install Tesseract OCR and set the path to the executable in the script.
2. Install the Python dependencies: Pillow, pytesseract, pandas, and openpyxl.
3. Put your photos in a folder named `data`.
4. Run the script.