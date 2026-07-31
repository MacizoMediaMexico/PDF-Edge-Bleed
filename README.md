# PDF Edge Bleed

Desktop app for generating mirror bleed on PDF files for professional print.

## Features

- **Vector Mirror Bleed (reduced file size)** — Bleed is created by reflecting the original vector page (Form XObject + clip), like Acrobat's "Extend graphic elements". File size stays near-identical to the source instead of inflating with raster strips
- **Raster Bleed Mode** — Original content stays as vector; bleed strips are sampled from rendered edge pixels at the chosen DPI
- **Per-side White Edge Skip** — Skip white/empty pixels independently on top, bottom, left, and right edges (sliders can be linked with the `&` button)
- **Adjustable DPI** — Raster bleed resolution from 300 to 600 DPI (snaps to 30-step increments)
- **Flatten Transparency Mode** — Rasterizes the page for consistent compositing with bleed
- **Drag & Drop** — Drop one or multiple PDFs onto the window or use "Add PDFs"
- **Light / Dark Theme** — Toggle between light and dark mode
- **Multi-page PDF support**
- **Automatic colorspace detection** (CMYK / RGB)

## Requirements

- Python 3.13+
- [PyMuPDF](https://pypi.org/project/PyMuPDF/) (fitz)
- [customtkinter](https://pypi.org/project/customtkinter/)
- [Pillow](https://pypi.org/project/Pillow/)

## Usage

### Run from source

```bash
pip install pymupdf customtkinter Pillow
python pdf_edge_bleed.py
```

### Build .app (macOS)

```bash
pip install pyinstaller
pyinstaller "PDF Edge Bleed.spec" --clean --noconfirm
```

The `.app` will be in the `dist/` folder.

## How it works

1. The original PDF page is placed as vector content on a larger page (extended by 0.125" on each side)
2. **Vector mode**: the page is reflected about each edge (and corner) using a `cm` mirror matrix, clipped to the bleed area — no image data is duplicated
3. **Raster mode**: edge strips are sampled from the rendered page at the chosen DPI, mirrored (flipped) and placed in the bleed area
4. White edge skip lets you trim empty pixels from the original edge before sampling
5. Flatten transparency mode rasterizes the full page to avoid compositing artifacts

## Version

**V2.03** — Vector mirror bleed mode (reduced file size) • Linked white-edge-skip sliders • Drag & drop

Made by **Douglas C.**
