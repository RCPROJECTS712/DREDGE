#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DREDGE - see what's eating your disk, fast.
-------------------------------------------
Point it at a drive or folder. It scans locally (nothing leaves your machine),
then opens a single self-contained HTML report with:

  * a ZOOMABLE SUNBURST WHEEL   - click a slice to drill in, breadcrumb to climb
  * a TREEMAP                   - the classic "big rectangles = big folders" view
  * a FILE-TYPE breakdown       - where your bytes go by kind (video/iso/etc)
  * TOP FILES and TOP FOLDERS   - the actual heavyweights, ranked

Pure Python standard library. No pip installs, no external scripts, no CDN.
The report is one .html file you can keep or share; it works offline.

Honest note: this walks the filesystem, so a full multi-hundred-GB C: drive
can take a couple of minutes. Point it at a folder (Downloads, a drive, Users)
and it's near-instant. It reports logical file sizes.
"""

import os
import sys
import json
import time
import stat as statmod
import heapq
import queue
import threading
import tempfile
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from string import ascii_uppercase

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
TOP_COLLECT   = 400     # how many largest files to keep during the scan
TOP_SHOW      = 100     # how many to list in the report
PRUNE_FRAC    = 0.0005  # folders below this fraction of total are folded away
MAX_DEPTH     = 7       # how deep the wheel data goes
CHILD_CAP     = 14      # max named children per node (rest folded to "other")

BG       = "#050805"
PANEL    = "#0a0f0a"
GREEN    = "#33ff66"
DIMGREEN = "#1f7a3d"
FONT     = ("Consolas", 10)
FONT_B   = ("Consolas", 10, "bold")
FONT_BIG = ("Consolas", 14, "bold")

# ----------------------------------------------------------------------------
# FILE-TYPE BUCKETS
# ----------------------------------------------------------------------------
_CAT = {
    "Video":  "mp4 mkv avi mov wmv flv webm m4v mpg mpeg ts m2ts vob 3gp",
    "Audio":  "mp3 flac wav aac m4a ogg wma opus aiff mid",
    "Images": "jpg jpeg png gif bmp tif tiff webp heic raw cr2 nef psd svg ico",
    "Documents": "pdf doc docx xls xlsx ppt pptx txt rtf odt ods epub mobi csv md",
    "Archives":  "zip rar 7z tar gz bz2 xz cab tgz zst lz4",
    "Disk images": "iso img vhd vhdx vmdk dmg bin cue nrg mds mdf",
    "Apps & installers": "exe msi msix appx appxbundle bat cmd ps1 com scr jar",
    "Code": "py js ts tsx jsx c cpp h hpp cs go rs rb php html css json xml "
            "sql sh gd tscn tres lua kt swift yml yaml toml ini",
    "Libraries": "dll so lib sys dylib pdb",
}
CATMAP = {}
for _cat, _exts in _CAT.items():
    for _e in _exts.split():
        CATMAP[_e] = _cat


# ----------------------------------------------------------------------------
# SCANNER
# ----------------------------------------------------------------------------
def _is_reparse(entry):
    """True for symlinks/junctions - skip them to avoid loops & double counts."""
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
        return bool(attrs & statmod.FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        try:
            return entry.is_symlink()
        except OSError:
            return False


def scan_tree(root, cancel, progress):
    """Walk `root`. Returns a result dict, or None if cancelled early."""
    ctr = {"files": 0, "dirs": 0, "skipped": 0, "n": 0}
    top = []                       # min-heap of (size, path)
    bycat = {}                     # category -> [bytes, count]
    all_dirs = []                  # (size, path, filecount) for top-folders
    t0 = time.time()

    def walk(path, name):
        node = {"n": name, "p": path, "s": 0, "d": 0, "f": 0, "c": []}
        try:
            it = os.scandir(path)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            ctr["skipped"] += 1
            return node
        with it:
            for entry in it:
                if cancel.is_set():
                    break
                try:
                    if _is_reparse(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        child = walk(entry.path, entry.name)
                        node["c"].append(child)
                        node["s"] += child["s"]
                        ctr["dirs"] += 1
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            sz = entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            ctr["skipped"] += 1
                            continue
                        node["s"] += sz
                        node["d"] += sz
                        node["f"] += 1
                        ctr["files"] += 1
                        if len(top) < TOP_COLLECT:
                            heapq.heappush(top, (sz, entry.path))
                        elif sz > top[0][0]:
                            heapq.heapreplace(top, (sz, entry.path))
                        ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
                        b = bycat.setdefault(CATMAP.get(ext, "Other"), [0, 0])
                        b[0] += sz
                        b[1] += 1
                        ctr["n"] += 1
                        if ctr["n"] % 1500 == 0:
                            progress(ctr["n"], entry.path)
                except OSError:
                    ctr["skipped"] += 1
                    continue
        all_dirs.append((node["s"], path, node["f"]))
        return node

    tree = walk(root, root)
    if cancel.is_set():
        return None

    top_files = sorted(top, key=lambda x: x[0], reverse=True)[:TOP_SHOW]
    top_folders = heapq.nlargest(TOP_SHOW, all_dirs, key=lambda x: x[0])

    return {
        "root": root,
        "tree": tree,
        "top_files": [{"path": p, "bytes": s} for s, p in top_files],
        "top_folders": [{"path": p, "bytes": s, "files": f}
                        for s, p, f in top_folders],
        "bycat": sorted(
            ({"name": k, "bytes": v[0], "count": v[1]} for k, v in bycat.items()),
            key=lambda x: x["bytes"], reverse=True),
        "total": tree["s"],
        "files": ctr["files"],
        "dirs": ctr["dirs"],
        "skipped": ctr["skipped"],
        "elapsed": round(time.time() - t0, 1),
    }


def prune(node, threshold, depth):
    """Shrink the full tree to a compact one for the wheel/treemap."""
    out = {"n": node["n"], "s": node["s"]}
    kids = []
    if depth < MAX_DEPTH:
        big = [c for c in node["c"] if c["s"] >= threshold]
        big.sort(key=lambda c: c["s"], reverse=True)
        for c in big[:CHILD_CAP]:
            kids.append(prune(c, threshold, depth + 1))
    if kids:
        leftover = node["s"] - sum(k["s"] for k in kids)
        if leftover > 0:                     # keep rings summing to the parent
            kids.append({"n": "[other]", "s": leftover})
        out["c"] = kids
    return out


# ----------------------------------------------------------------------------
# REPORT
# ----------------------------------------------------------------------------
def build_report(result):
    threshold = max(1, int(result["total"] * PRUNE_FRAC))
    data = {
        "root": result["root"],
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "total": result["total"],
        "files": result["files"],
        "dirs": result["dirs"],
        "skipped": result["skipped"],
        "elapsed": result["elapsed"],
        "tree": prune(result["tree"], threshold, 0),
        "byCat": result["bycat"],
        "topFiles": result["top_files"],
        "topFolders": result["top_folders"],
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("/*__DATA__*/", payload)


def write_and_open(html_text, root):
    safe = "".join(ch if ch.isalnum() else "_" for ch in root)[:40] or "scan"
    fname = "dredge_%s_%s.html" % (safe, time.strftime("%Y%m%d_%H%M%S"))
    for folder in (os.path.dirname(os.path.abspath(__file__)),
                   tempfile.gettempdir()):
        try:
            path = os.path.join(folder, fname)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_text)
            webbrowser.open("file:///" + path.replace("\\", "/"))
            return path
        except OSError:
            continue
    raise OSError("Could not write the report anywhere.")


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class Dredge(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DREDGE  -  disk space visualizer")
        self.configure(bg=BG)
        self.geometry("640x420")
        self.minsize(560, 380)

        self.cancel = threading.Event()
        self.q = queue.Queue()
        self.worker = None

        self._build()
        self.after(120, self._poll)

    def _build(self):
        tk.Label(self, text="// DREDGE", bg=BG, fg=GREEN,
                 font=FONT_BIG).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(self, text="what's eating your disk", bg=BG, fg=DIMGREEN,
                 font=FONT).pack(anchor="w", padx=12)

        # target row
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=12, pady=(14, 4))
        tk.Label(row, text="TARGET", bg=BG, fg=GREEN, font=FONT_B).pack(
            side="left")
        self.target = tk.Entry(row, bg=PANEL, fg=GREEN, insertbackground=GREEN,
                               font=FONT, relief="flat")
        self.target.pack(side="left", fill="x", expand=True, padx=8)
        self.target.insert(0, os.path.expanduser("~"))
        self._btn(row, "BROWSE", self.browse).pack(side="left")

        # drive shortcuts
        drives = tk.Frame(self, bg=BG)
        drives.pack(fill="x", padx=12, pady=2)
        tk.Label(drives, text="DRIVES", bg=BG, fg=DIMGREEN,
                 font=FONT).pack(side="left", padx=(0, 6))
        for d in ascii_uppercase:
            p = "%s:\\" % d
            if os.path.exists(p):
                self._btn(drives, d, lambda pp=p: self._set_target(pp),
                          small=True).pack(side="left", padx=2)

        # controls
        ctl = tk.Frame(self, bg=BG)
        ctl.pack(fill="x", padx=12, pady=(12, 4))
        self.scan_btn = self._btn(ctl, "SCAN", self.start)
        self.scan_btn.pack(side="left")
        self.cancel_btn = self._btn(ctl, "CANCEL", self.stop, danger=True)
        self.cancel_btn.pack(side="left", padx=8)
        self.cancel_btn.config(state="disabled")

        # progress
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.pack(fill="x", padx=12, pady=(10, 2))
        self.count_lbl = tk.Label(self, text="", bg=BG, fg=GREEN, font=FONT,
                                  anchor="w")
        self.count_lbl.pack(fill="x", padx=12)
        self.path_lbl = tk.Label(self, text="", bg=BG, fg=DIMGREEN, font=FONT,
                                 anchor="w", wraplength=600, justify="left")
        self.path_lbl.pack(fill="x", padx=12)

        self.status = tk.Label(self, text="Pick a target and hit SCAN.",
                               bg="#0f160f", fg=GREEN, font=FONT,
                               anchor="w", padx=8)
        self.status.pack(fill="x", side="bottom")

    def _btn(self, parent, text, cmd, danger=False, small=False):
        fg = "#ff6b6b" if danger else GREEN
        return tk.Button(parent, text=text, command=cmd, bg=PANEL, fg=fg,
                         activebackground="#04331c", activeforeground=fg,
                         font=FONT_B, bd=1, relief="ridge",
                         padx=(4 if small else 10), pady=2, cursor="hand2")

    def _set_target(self, p):
        self.target.delete(0, "end")
        self.target.insert(0, p)

    def browse(self):
        d = filedialog.askdirectory(title="Choose a folder or drive to scan")
        if d:
            self._set_target(os.path.normpath(d))

    # -- scan lifecycle ------------------------------------------------------
    def start(self):
        root = self.target.get().strip().strip('"')
        if not root or not os.path.isdir(root):
            messagebox.showerror("Not a folder",
                                 "That path isn't a folder I can scan.")
            return
        self.cancel.clear()
        self.scan_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.bar.start(12)
        self.status.config(text="Scanning %s ..." % root)
        self.count_lbl.config(text="")
        self.path_lbl.config(text="")

        def job():
            try:
                res = scan_tree(root, self.cancel,
                                lambda n, p: self.q.put(("prog", n, p)))
                self.q.put(("done", res))
            except Exception as e:               # never die silently
                self.q.put(("error", str(e)))

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def stop(self):
        self.cancel.set()
        self.status.config(text="Cancelling...")

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "prog":
                    self.count_lbl.config(text="%s files scanned" %
                                          format(msg[1], ","))
                    self.path_lbl.config(text=msg[2])
                elif msg[0] == "done":
                    self._finish(msg[1])
                elif msg[0] == "error":
                    self._reset()
                    messagebox.showerror("Scan error", msg[1])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _finish(self, res):
        self.bar.stop()
        if res is None:
            self._reset()
            self.status.config(text="Cancelled.")
            return
        self.status.config(text="Building report...")
        self.update_idletasks()
        try:
            path = write_and_open(build_report(res), res["root"])
        except Exception as e:
            self._reset()
            messagebox.showerror("Report error", str(e))
            return
        self._reset()
        gb = res["total"] / (1024 ** 3)
        self.status.config(
            text="Done - %.1f GB across %s files. Report: %s" %
            (gb, format(res["files"], ","), path))

    def _reset(self):
        self.bar.stop()
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")


# ----------------------------------------------------------------------------
# HTML / CSS / JS TEMPLATE  (self-contained, vanilla, no external anything)
# ----------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DREDGE report</title>
<style>
  :root{ --bg:#050805; --panel:#0b110b; --green:#33ff66; --dim:#1f7a3d;
         --line:#15321d; --text:#cfeede; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:14px}
  h1{color:var(--green);margin:0;font-size:20px;letter-spacing:1px}
  .sub{color:var(--dim);font-size:12px}
  header{padding:16px 20px;border-bottom:1px solid var(--line)}
  .cards{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
        padding:10px 14px;min-width:120px}
  .card .k{color:var(--dim);font-size:11px;text-transform:uppercase}
  .card .v{color:var(--green);font-size:18px;font-weight:bold;margin-top:2px}
  main{padding:18px 20px;max-width:1200px;margin:0 auto}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:16px;margin-bottom:20px}
  .panel h2{margin:0 0 4px;color:var(--green);font-size:15px}
  .panel .hint{color:var(--dim);font-size:12px;margin-bottom:12px}
  .viz{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}
  #crumbs{margin-bottom:10px;font-size:13px;color:var(--dim);
          display:flex;flex-wrap:wrap;gap:4px;align-items:center}
  #crumbs a{color:var(--green);cursor:pointer;text-decoration:none}
  #crumbs a:hover{text-decoration:underline}
  #crumbs span.sep{color:var(--line)}
  svg{display:block;overflow:visible}
  .seg{cursor:pointer;stroke:#050805;stroke-width:0.6}
  .seg:hover{opacity:.82}
  .tile{cursor:pointer;stroke:#050805;stroke-width:1.5}
  .tile:hover{opacity:.82}
  .tlabel{fill:#04140a;font-size:11px;font-weight:bold;pointer-events:none}
  .centerT{fill:var(--green);font-weight:bold}
  .centerS{fill:var(--text)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--dim);text-transform:uppercase;font-size:11px}
  td.sz{color:var(--green);text-align:right;white-space:nowrap}
  td.path{color:var(--text);word-break:break-all}
  .barrow{display:grid;grid-template-columns:150px 1fr 150px;gap:10px;
          align-items:center;margin:5px 0}
  .barrow .nm{color:var(--text)}
  .track{background:#0a0a0a;border:1px solid var(--line);border-radius:4px;
         height:16px;overflow:hidden}
  .fill{height:100%}
  .barrow .val{color:var(--green);text-align:right;font-size:12px}
  #tip{position:fixed;pointer-events:none;background:#04140a;
       border:1px solid var(--green);border-radius:6px;padding:6px 9px;
       font-size:12px;color:var(--text);display:none;max-width:340px;z-index:9}
  #tip b{color:var(--green)}
  .cols{display:flex;gap:20px;flex-wrap:wrap}
  .cols>div{flex:1;min-width:320px}
</style></head>
<body>
<header>
  <h1>// DREDGE</h1>
  <div class="sub" id="rootline"></div>
  <div class="cards" id="cards"></div>
</header>
<main>
  <div class="panel">
    <h2>Folder map</h2>
    <div class="hint">Click any slice or tile to drill in. Use the breadcrumb
      to climb back out. The wheel and the treemap show the same folder.</div>
    <div id="crumbs"></div>
    <div class="viz">
      <svg id="wheel" width="520" height="520" viewBox="0 0 520 520"></svg>
      <svg id="tree" width="620" height="440" viewBox="0 0 620 440"
           style="flex:1;min-width:360px"></svg>
    </div>
  </div>

  <div class="panel">
    <h2>Where your bytes go</h2>
    <div class="hint">Total size by file type across the whole scan.</div>
    <div id="cats"></div>
  </div>

  <div class="panel">
    <div class="cols">
      <div>
        <h2>Biggest files</h2>
        <div class="hint">The single heaviest files found.</div>
        <table><thead><tr><th>#</th><th>Size</th><th>Path</th></tr></thead>
          <tbody id="topfiles"></tbody></table>
      </div>
      <div>
        <h2>Biggest folders</h2>
        <div class="hint">Folders by total contents.</div>
        <table><thead><tr><th>#</th><th>Size</th><th>Files</th><th>Path</th></tr>
          </thead><tbody id="topfolders"></tbody></table>
      </div>
    </div>
  </div>
  <div class="sub" id="genline" style="text-align:center;padding-bottom:20px">
  </div>
</main>
<div id="tip"></div>

<script>
const DATA = /*__DATA__*/;

// ---- helpers ----
function human(b){
  if(b<1024) return b+" B";
  const u=["KB","MB","GB","TB","PB"]; let i=-1;
  do{ b/=1024; i++; }while(b>=1024 && i<u.length-1);
  return b.toFixed(b<10?1:0)+" "+u[i];
}
function pct(x,t){ return t>0 ? (100*x/t).toFixed(1)+"%" : "0%"; }
const PALETTE=["#33ff8a","#2fd0e0","#ffd23f","#ff7a59","#c07bff","#7be0a3",
  "#e04f8f","#4f9bff","#9bd94f","#ff9f43","#5fe3c0","#d98cff","#8affb0",
  "#ff6b6b"];

// ---- state ----
let path=[DATA.tree];
function cur(){ return path[path.length-1]; }

// ---- header ----
document.getElementById("rootline").textContent =
  "target: "+DATA.root+"   -   scanned "+DATA.generated;
document.getElementById("genline").textContent =
  "Generated by DREDGE. Logical file sizes. "+
  (DATA.skipped? DATA.skipped+" items skipped (no permission / in use).":"");
const cards=[
  ["total size", human(DATA.total)],
  ["files", DATA.files.toLocaleString()],
  ["folders", DATA.dirs.toLocaleString()],
  ["scan time", DATA.elapsed+" s"],
];
document.getElementById("cards").innerHTML = cards.map(c=>
  '<div class="card"><div class="k">'+c[0]+'</div><div class="v">'+c[1]+
  '</div></div>').join("");

// ---- tooltip ----
const tip=document.getElementById("tip");
function showTip(html,e){ tip.innerHTML=html; tip.style.display="block";
  moveTip(e); }
function moveTip(e){ const p=14;
  let x=e.clientX+p, y=e.clientY+p;
  if(x+tip.offsetWidth>innerWidth) x=e.clientX-tip.offsetWidth-p;
  if(y+tip.offsetHeight>innerHeight) y=e.clientY-tip.offsetHeight-p;
  tip.style.left=x+"px"; tip.style.top=y+"px"; }
function hideTip(){ tip.style.display="none"; }

// ---- breadcrumb ----
function renderCrumbs(){
  const el=document.getElementById("crumbs"); el.innerHTML="";
  path.forEach((n,i)=>{
    if(i){ const s=document.createElement("span"); s.className="sep";
      s.textContent=" / "; el.appendChild(s); }
    const a=document.createElement("a");
    a.textContent = i? n.n : "root";
    a.onclick=()=>{ path=path.slice(0,i+1); render(); };
    el.appendChild(a);
  });
}

// ---- SUNBURST ----
const WSIZE=520, WC=WSIZE/2, RC=62, TR=46, RINGS=4;
function polar(r,a){ return [WC+r*Math.sin(a), WC-r*Math.cos(a)]; }
function arc(r0,r1,a0,a1){
  const big=(a1-a0)>Math.PI?1:0;
  const [x0,y0]=polar(r1,a0),[x1,y1]=polar(r1,a1);
  const [x2,y2]=polar(r0,a1),[x3,y3]=polar(r0,a0);
  return "M"+x0+" "+y0+" A"+r1+" "+r1+" 0 "+big+" 1 "+x1+" "+y1+
         " L"+x2+" "+y2+" A"+r0+" "+r0+" 0 "+big+" 0 "+x3+" "+y3+" Z";
}
function renderWheel(){
  const svg=document.getElementById("wheel"); svg.innerHTML="";
  const focus=cur(); const segs=[];
  (function layout(node,depth,a0,a1,hue,chain){
    if(depth>RINGS) return;
    if(depth>=1) segs.push({node,depth,a0,a1,hue,chain});
    if(!node.c||!node.c.length) return;
    let a=a0; const span=a1-a0; const tot=node.s||1;
    node.c.forEach((ch,i)=>{
      const f=(ch.s||0)/tot; const b0=a, b1=a+span*Math.max(f,0); a=b1;
      const h = depth===0 ? (i/node.c.length)*360 : hue;
      layout(ch,depth+1,b0,b1,h,chain.concat([ch]));
    });
  })(focus,0,0,Math.PI*2,0,[]);

  const NS="http://www.w3.org/2000/svg";
  segs.forEach(s=>{
    if(s.a1-s.a0 < 0.004) return;              // too thin to see/click
    const r0=RC+(s.depth-1)*TR, r1=r0+TR-1.5;
    const p=document.createElementNS(NS,"path");
    p.setAttribute("d",arc(r0,r1,s.a0,s.a1));
    p.setAttribute("class","seg");
    p.setAttribute("fill","hsl("+s.hue+",60%,"+(38+s.depth*7)+"%)");
    const full=[DATA.root].concat(path.slice(1).map(x=>x.n),
                                  s.chain.map(x=>x.n)).join(" / ");
    p.addEventListener("mousemove",e=>showTip(
      "<b>"+esc(s.node.n)+"</b><br>"+human(s.node.s)+
      "  ("+pct(s.node.s,focus.s)+" of view)<br><span style='color:#1f7a3d'>"+
      esc(full)+"</span>",e));
    p.addEventListener("mouseleave",hideTip);
    if(s.node.c&&s.node.c.length){
      p.addEventListener("click",()=>{ hideTip();
        path=path.concat(s.chain); render(); });
    }
    svg.appendChild(p);
  });

  // center = current focus, click to go up
  const c=document.createElementNS(NS,"circle");
  c.setAttribute("cx",WC); c.setAttribute("cy",WC); c.setAttribute("r",RC-2);
  c.setAttribute("fill","#0b110b"); c.setAttribute("stroke","#15321d");
  if(path.length>1){ c.style.cursor="pointer";
    c.addEventListener("click",()=>{ path.pop(); render(); }); }
  svg.appendChild(c);
  const t1=document.createElementNS(NS,"text");
  t1.setAttribute("x",WC); t1.setAttribute("y",WC-4);
  t1.setAttribute("text-anchor","middle"); t1.setAttribute("class","centerT");
  t1.setAttribute("font-size","13");
  t1.textContent = trunc(path.length>1?cur().n:"root",16);
  svg.appendChild(t1);
  const t2=document.createElementNS(NS,"text");
  t2.setAttribute("x",WC); t2.setAttribute("y",WC+14);
  t2.setAttribute("text-anchor","middle"); t2.setAttribute("class","centerS");
  t2.setAttribute("font-size","12"); t2.textContent=human(cur().s);
  svg.appendChild(t2);
  if(path.length>1){
    const t3=document.createElementNS(NS,"text");
    t3.setAttribute("x",WC); t3.setAttribute("y",WC+30);
    t3.setAttribute("text-anchor","middle"); t3.setAttribute("fill","#1f7a3d");
    t3.setAttribute("font-size","10"); t3.textContent="click to go up";
    svg.appendChild(t3);
  }
}

// ---- TREEMAP (squarified) ----
function squarify(children,x,y,w,h){
  const out=[]; const tot=children.reduce((s,c)=>s+(c.s||0),0)||1;
  const scale=(w*h)/tot;
  let items=children.map(c=>({c,area:(c.s||0)*scale})).filter(o=>o.area>0);
  let rx=x,ry=y,rw=w,rh=h;
  function worst(row,len){
    const s=row.reduce((a,o)=>a+o.area,0);
    let mx=0,mn=Infinity; row.forEach(o=>{mx=Math.max(mx,o.area);
      mn=Math.min(mn,o.area);});
    const s2=s*s, l2=len*len;
    return Math.max((l2*mx)/s2,(s2)/(l2*mn));
  }
  function layoutRow(row,horizontal){
    const s=row.reduce((a,o)=>a+o.area,0);
    if(horizontal){
      const rowH=s/rw; let cx=rx;
      row.forEach(o=>{ const cw=o.area/rowH;
        out.push({c:o.c,x:cx,y:ry,w:cw,h:rowH}); cx+=cw; });
      ry+=rowH; rh-=rowH;
    }else{
      const rowW=s/rh; let cy=ry;
      row.forEach(o=>{ const ch=o.area/rowW;
        out.push({c:o.c,x:rx,y:cy,w:rowW,h:ch}); cy+=ch; });
      rx+=rowW; rw-=rowW;
    }
  }
  let row=[];
  while(items.length){
    const horizontal = rw>=rh;
    const len = horizontal? rw : rh;
    const next=items[0];
    if(row.length===0){ row.push(next); items.shift(); continue; }
    const w1=worst(row,len);
    const w2=worst(row.concat([next]),len);
    if(w2<=w1){ row.push(next); items.shift(); }
    else{ layoutRow(row,horizontal); row=[]; }
  }
  if(row.length) layoutRow(row, rw>=rh);
  return out;
}
function renderTree(){
  const svg=document.getElementById("tree");
  const W=620,H=440; svg.innerHTML="";
  const focus=cur();
  const kids=(focus.c||[]).slice().sort((a,b)=>(b.s||0)-(a.s||0));
  const NS="http://www.w3.org/2000/svg";
  if(!kids.length){
    const t=document.createElementNS(NS,"text");
    t.setAttribute("x",W/2); t.setAttribute("y",H/2);
    t.setAttribute("text-anchor","middle"); t.setAttribute("fill","#1f7a3d");
    t.textContent="(no sub-folders to break down here)";
    svg.appendChild(t); return;
  }
  const rects=squarify(kids,2,2,W-4,H-4);
  rects.forEach((r,i)=>{
    const hue=(i/rects.length)*360;
    const g=document.createElementNS(NS,"rect");
    g.setAttribute("x",r.x);g.setAttribute("y",r.y);
    g.setAttribute("width",Math.max(0,r.w));g.setAttribute("height",Math.max(0,r.h));
    g.setAttribute("class","tile");
    g.setAttribute("fill",PALETTE[i%PALETTE.length]);
    g.addEventListener("mousemove",e=>showTip(
      "<b>"+esc(r.c.n)+"</b><br>"+human(r.c.s)+" ("+pct(r.c.s,focus.s)+
      " of view)",e));
    g.addEventListener("mouseleave",hideTip);
    if(r.c.c&&r.c.c.length)
      g.addEventListener("click",()=>{hideTip();path.push(r.c);render();});
    svg.appendChild(g);
    if(r.w>62 && r.h>26){
      const t=document.createElementNS(NS,"text");
      t.setAttribute("x",r.x+6); t.setAttribute("y",r.y+16);
      t.setAttribute("class","tlabel");
      t.textContent=trunc(r.c.n,Math.floor(r.w/7));
      svg.appendChild(t);
      if(r.h>42){
        const s=document.createElementNS(NS,"text");
        s.setAttribute("x",r.x+6); s.setAttribute("y",r.y+31);
        s.setAttribute("class","tlabel");
        s.setAttribute("style","font-weight:normal;fill:#052a12");
        s.textContent=human(r.c.s); svg.appendChild(s);
      }
    }
  });
}

// ---- type bars ----
function renderCats(){
  const el=document.getElementById("cats");
  const max=DATA.byCat.reduce((m,c)=>Math.max(m,c.bytes),1);
  el.innerHTML=DATA.byCat.map((c,i)=>
    '<div class="barrow"><div class="nm">'+esc(c.name)+'</div>'+
    '<div class="track"><div class="fill" style="width:'+
      (100*c.bytes/max)+'%;background:'+PALETTE[i%PALETTE.length]+'"></div></div>'+
    '<div class="val">'+human(c.bytes)+'  ('+pct(c.bytes,DATA.total)+
      ', '+c.count.toLocaleString()+' files)</div></div>').join("");
}

// ---- tables ----
function renderTables(){
  document.getElementById("topfiles").innerHTML = DATA.topFiles.map((f,i)=>
    '<tr><td>'+(i+1)+'</td><td class="sz">'+human(f.bytes)+
    '</td><td class="path">'+esc(f.path)+'</td></tr>').join("");
  document.getElementById("topfolders").innerHTML = DATA.topFolders.map((f,i)=>
    '<tr><td>'+(i+1)+'</td><td class="sz">'+human(f.bytes)+
    '</td><td class="sz">'+f.files.toLocaleString()+
    '</td><td class="path">'+esc(f.path)+'</td></tr>').join("");
}

// ---- utils ----
function esc(s){ return String(s).replace(/[&<>"]/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function trunc(s,n){ s=String(s); return s.length>n? s.slice(0,n-1)+"\u2026":s; }
document.addEventListener("mousemove",e=>{ if(tip.style.display==="block")
  moveTip(e); });

// ---- go ----
function render(){ renderCrumbs(); renderWheel(); renderTree(); }
renderCats(); renderTables(); render();
</script>
</body></html>
"""


def main():
    if os.name != "nt":
        print("DREDGE reads the local filesystem; built for Windows but the "
              "scan works anywhere Python runs.")
    Dredge().mainloop()


if __name__ == "__main__":
    main()
