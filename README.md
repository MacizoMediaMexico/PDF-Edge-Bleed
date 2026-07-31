# PDF Edge Bleed

Desktop app for generating mirror bleed on PDF files, preserving vector content and adding raster-based edge extension bleed for professional print.

## Features

- **Vector + Raster Bleed** — Original content stays as vector; bleed is generated from rendered edge strips
- **Per-side White Edge Skip** — Skip white/empty pixels independently on top, bottom, left, and right edges
- **Adjustable DPI** — Bleed resolution from 300 to 600 DPI (snaps to 30-step increments)
- **Flatten Transparency Mode** — Rasterizes the page for consistent compositing with bleed
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
2. Edge strips are sampled from the rendered page at the chosen DPI
3. Strips are mirrored (flipped) and placed in the bleed area
4. White edge skip lets you trim empty pixels from the original edge before sampling
5. Flatten transparency mode rasterizes the full page to avoid compositing artifacts

## Version

**V2.0** — Built with customtkinter • Light/Dark theme • Per-side inset controls

Made by **Douglas C.**
