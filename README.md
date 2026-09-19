[README.txt](https://github.com/user-attachments/files/32422967/README.txt)
DREDGE - see what's eating your disk
====================================

WHAT IT DOES
------------
Point it at a drive or folder. It scans locally (nothing leaves your machine)
and opens ONE self-contained HTML report you can keep or share. Inside:

  * ZOOMABLE SUNBURST WHEEL - the rings are your folder tree. Click any slice
    to drill into that folder; both the wheel and the treemap re-center on it.
    Click the middle (or a breadcrumb) to climb back out. Hover for size + %.
  * TREEMAP - the classic "big rectangles = big folders" view of the folder
    you're currently focused on. Click a tile to drill in.
  * WHERE YOUR BYTES GO - total size by file type (video, disk images,
    archives, images, code, etc) across the whole scan.
  * BIGGEST FILES / BIGGEST FOLDERS - the actual heavyweights, ranked.

Pure Python standard library. No pip installs, no external scripts, no CDN.
The report works offline forever.


HOW TO RUN
----------
Double-click run.bat  (needs Python from python.org, "Add to PATH" ticked).
Or from a terminal:  python dredge.py

Then: pick a TARGET (type a path, hit BROWSE, or click a drive letter) and
press SCAN. A report opens in your browser when it finishes. CANCEL stops a
long scan at any time.


READING IT
----------
- Start at the biggest wheel slices / treemap tiles - those are your space
  hogs. Click in to see what's inside them, keep clicking until you find the
  culprit. This is usually 3-4 clicks to whatever's eating your drive.
- The "[other]" slice inside a folder = its loose files plus anything too
  small to draw on its own. If "[other]" is huge, that folder is full of files
  rather than sub-folders - check the Biggest Files table.
- Type breakdown tells you the KIND of thing eating space. Big "Disk images"?
  Old ISOs. Big "Video"? Downloads/recordings. Big "Archives"? Old zips.


HONEST LIMITS
-------------
- It walks the filesystem, so a full multi-hundred-GB C: drive can take a
  couple of minutes. A specific folder or a smaller drive is near-instant.
  (Native tools that read the raw NTFS index, like WizTree, scan a whole disk
  faster - but this one needs no install, runs anywhere, and gives you the
  visuals.)
- It reports LOGICAL file sizes. That can differ slightly from disk "used"
  because of cluster slack and NTFS compression. Good enough to find hogs.
- Symlinks and junctions are skipped so nothing gets double-counted or loops.
- Files it can't read (no permission / locked) are skipped and counted in the
  report header.


TUNING (optional)
-----------------
Open dredge.py and edit the CONFIG block near the top:
  TOP_SHOW   - how many files/folders to list (default 100)
  MAX_DEPTH  - how many folder levels the wheel data goes (default 7)
  CHILD_CAP  - max named slices per folder before the rest fold into [other]
The file-type buckets are the _CAT table just below CONFIG - add your own
extensions to any category.
