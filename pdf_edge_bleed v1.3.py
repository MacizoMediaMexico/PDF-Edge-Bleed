import fitz  # PyMuPDF
import os
import sys
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk
import threading
from PIL import Image
import io


def detect_colorspace(input_path):
    """
    Detecta el espacio de color del PDF (CMYK o RGB).
    """
    try:
        doc = fitz.open(input_path)
        page = doc[0]
        image_list = page.get_images(full=True)

        cmyk_count = 0
        rgb_count = 0

        for img in image_list:
            xref = img[0]
            try:
                img_info = doc.extract_image(xref)
                if img_info and "colorspace" in img_info:
                    cs = img_info["colorspace"]
                    if cs == 4:
                        cmyk_count += 1
                    elif cs == 3:
                        rgb_count += 1
            except Exception:
                pass

        doc.close()

        if cmyk_count > rgb_count:
            return "CMYK"
        if rgb_count > cmyk_count:
            return "RGB"
        return "CMYK"

    except Exception as e:
        print(f"Error detecting colorspace: {e}")
        return "CMYK"


def clamp_crop_box(left, top, right, bottom, image_width, image_height):
    """
    Mantiene el crop dentro del render, incluso si el usuario sube mucho el inset.
    """
    left = max(0, min(left, image_width - 1))
    top = max(0, min(top, image_height - 1))
    right = max(left + 1, min(right, image_width))
    bottom = max(top + 1, min(bottom, image_height))
    return left, top, right, bottom


def create_mirror_bleed(
    input_path,
    output_path,
    log_callback=None,
    progress_callback=None,
    edge_sample_inches=0.05,
    bleed_dpi=360,
    edge_inset_px=0,
    flatten_page=False,
):
    """
    EDGE EXTENSION METHOD: Vector content + raster bleed.

    - Original content stays as vector.
    - Bleed is generated from rendered edge strips.
    - flatten_page rasterizes the original page from the same render used for
      bleed generation. This helps with transparency/gradient overlays that
      otherwise composite differently against raster bleed.
    - edge_inset_px lets you skip white/empty pixels at the original PDF edge.
      It can be a single integer or a dict with top/bottom/left/right values.
      Example: if only the top edge has 12 white pixels, set top to 12 px and
      the other sides to 0.
    """
    doc = None
    new_doc = None

    try:
        bleed_inches = 0.125
        inch_to_points = 72
        bleed_pts = bleed_inches * inch_to_points

        colorspace = detect_colorspace(input_path)

        dpi_scale = bleed_dpi / 72.0
        edge_sample_pts = edge_sample_inches * inch_to_points
        sample_px = max(2, int(edge_sample_pts * dpi_scale))
        bleed_px = max(1, int(bleed_pts * dpi_scale))
        outer_bleed_overlap_pts = 2 / dpi_scale
        if isinstance(edge_inset_px, dict):
            edge_insets = {
                "top": max(0, int(edge_inset_px.get("top", 0))),
                "bottom": max(0, int(edge_inset_px.get("bottom", 0))),
                "left": max(0, int(edge_inset_px.get("left", 0))),
                "right": max(0, int(edge_inset_px.get("right", 0))),
            }
        else:
            inset_value = max(0, int(edge_inset_px))
            edge_insets = {
                "top": inset_value,
                "bottom": inset_value,
                "left": inset_value,
                "right": inset_value,
            }

        seam_cover_px = {side: min(value, 24) for side, value in edge_insets.items()}
        seam_cover_pts = {side: value / dpi_scale for side, value in seam_cover_px.items()}

        doc = fitz.open(input_path)
        new_doc = fitz.open()
        total_pages = len(doc)

        if log_callback:
            log_callback(f"Edge Extension Engine - Vector + {bleed_dpi} DPI Bleed")
            log_callback(f"Color Space Detected: {colorspace}")
            log_callback("Bleed Render Mode: RGB appearance match")
            log_callback(f"Flatten Transparency Mode: {'ON' if flatten_page else 'OFF'}")
            log_callback(f"Edge Sample: {edge_sample_inches:.2f}\" (~{sample_px}px)")
            log_callback(
                "White Edge Skip: "
                f"Top {edge_insets['top']}px | Bottom {edge_insets['bottom']}px | "
                f"Left {edge_insets['left']}px | Right {edge_insets['right']}px"
            )
            log_callback(
                "Inner Seam Cover: "
                f"Top {seam_cover_px['top']}px | Bottom {seam_cover_px['bottom']}px | "
                f"Left {seam_cover_px['left']}px | Right {seam_cover_px['right']}px"
            )
            log_callback("Outer Edge Overlap: 2px")
            log_callback(f"Bleed Extension: {bleed_inches}\"")
            log_callback(f"Processing {total_pages} page(s)...")

        for page_index in range(total_pages):
            page = doc[page_index]
            rect = page.rect
            w, h = rect.width, rect.height

            new_w = w + (2 * bleed_pts)
            new_h = h + (2 * bleed_pts)

            new_page = new_doc.new_page(width=new_w, height=new_h)

            main_rect = fitz.Rect(bleed_pts, bleed_pts, w + bleed_pts, h + bleed_pts)

            pix = page.get_pixmap(
                matrix=fitz.Matrix(dpi_scale, dpi_scale),
                colorspace=fitz.csRGB,
                alpha=False,
            )
            page_img = Image.open(io.BytesIO(pix.tobytes("png")))
            page_img = page_img.convert("RGB")
            page_w_px, page_h_px = page_img.size

            if flatten_page:
                full_page_bytes = io.BytesIO()
                page_img.save(full_page_bytes, format="PNG")
                new_page.insert_image(main_rect, stream=full_page_bytes.getvalue())
            else:
                new_page.show_pdf_page(main_rect, doc, page_index)

            max_inset_x = max(0, (page_w_px - sample_px - 1) // 2)
            max_inset_y = max(0, (page_h_px - sample_px - 1) // 2)
            inset_top = min(edge_insets["top"], max_inset_y)
            inset_bottom = min(edge_insets["bottom"], max_inset_y)
            inset_left = min(edge_insets["left"], max_inset_x)
            inset_right = min(edge_insets["right"], max_inset_x)

            limited_insets = (
                inset_top != edge_insets["top"]
                or inset_bottom != edge_insets["bottom"]
                or inset_left != edge_insets["left"]
                or inset_right != edge_insets["right"]
            )
            if any(edge_insets.values()) and log_callback and limited_insets:
                log_callback(
                    f"  Page {page_index + 1}: one or more inset values were limited "
                    "because the page is small."
                )

            def crop_resize(box, size, flip_h=False, flip_v=False):
                box = clamp_crop_box(*box, page_w_px, page_h_px)
                strip = page_img.crop(box)
                strip = strip.resize(size, Image.LANCZOS)
                if flip_h:
                    strip = strip.transpose(Image.FLIP_LEFT_RIGHT)
                if flip_v:
                    strip = strip.transpose(Image.FLIP_TOP_BOTTOM)

                img_bytes = io.BytesIO()
                strip.save(img_bytes, format="PNG")
                return img_bytes.getvalue()

            def create_bleed_image(side, flip_h=False, flip_v=False):
                if side == "top":
                    return crop_resize(
                        (0, inset_top, page_w_px, inset_top + sample_px),
                        (page_w_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "bottom":
                    return crop_resize(
                        (0, page_h_px - inset_bottom - sample_px, page_w_px, page_h_px - inset_bottom),
                        (page_w_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "left":
                    return crop_resize(
                        (inset_left, 0, inset_left + sample_px, page_h_px),
                        (bleed_px, page_h_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "right":
                    return crop_resize(
                        (page_w_px - inset_right - sample_px, 0, page_w_px - inset_right, page_h_px),
                        (bleed_px, page_h_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "tl":
                    return crop_resize(
                        (inset_left, inset_top, inset_left + sample_px, inset_top + sample_px),
                        (bleed_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "tr":
                    return crop_resize(
                        (
                            page_w_px - inset_right - sample_px,
                            inset_top,
                            page_w_px - inset_right,
                            inset_top + sample_px,
                        ),
                        (bleed_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "bl":
                    return crop_resize(
                        (
                            inset_left,
                            page_h_px - inset_bottom - sample_px,
                            inset_left + sample_px,
                            page_h_px - inset_bottom,
                        ),
                        (bleed_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                if side == "br":
                    return crop_resize(
                        (
                            page_w_px - inset_right - sample_px,
                            page_h_px - inset_bottom - sample_px,
                            page_w_px - inset_right,
                            page_h_px - inset_bottom,
                        ),
                        (bleed_px, bleed_px),
                        flip_h=flip_h,
                        flip_v=flip_v,
                    )

                raise ValueError(f"Unknown bleed side: {side}")

            new_page.insert_image(
                fitz.Rect(
                    bleed_pts,
                    -outer_bleed_overlap_pts,
                    w + bleed_pts,
                    bleed_pts + seam_cover_pts["top"],
                ),
                stream=create_bleed_image("top", flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    bleed_pts,
                    h + bleed_pts - seam_cover_pts["bottom"],
                    w + bleed_pts,
                    new_h + outer_bleed_overlap_pts,
                ),
                stream=create_bleed_image("bottom", flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    -outer_bleed_overlap_pts,
                    bleed_pts,
                    bleed_pts + seam_cover_pts["left"],
                    h + bleed_pts,
                ),
                stream=create_bleed_image("left", flip_h=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    w + bleed_pts - seam_cover_pts["right"],
                    bleed_pts,
                    new_w + outer_bleed_overlap_pts,
                    h + bleed_pts,
                ),
                stream=create_bleed_image("right", flip_h=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    -outer_bleed_overlap_pts,
                    -outer_bleed_overlap_pts,
                    bleed_pts + seam_cover_pts["left"],
                    bleed_pts + seam_cover_pts["top"],
                ),
                stream=create_bleed_image("tl", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    w + bleed_pts - seam_cover_pts["right"],
                    -outer_bleed_overlap_pts,
                    new_w + outer_bleed_overlap_pts,
                    bleed_pts + seam_cover_pts["top"],
                ),
                stream=create_bleed_image("tr", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    -outer_bleed_overlap_pts,
                    h + bleed_pts - seam_cover_pts["bottom"],
                    bleed_pts + seam_cover_pts["left"],
                    new_h + outer_bleed_overlap_pts,
                ),
                stream=create_bleed_image("bl", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(
                    w + bleed_pts - seam_cover_pts["right"],
                    h + bleed_pts - seam_cover_pts["bottom"],
                    new_w + outer_bleed_overlap_pts,
                    new_h + outer_bleed_overlap_pts,
                ),
                stream=create_bleed_image("br", flip_h=True, flip_v=True),
            )

            if log_callback:
                log_callback(f"  Page {page_index + 1}/{total_pages} completed")

            if progress_callback:
                progress = ((page_index + 1) / total_pages) * 100
                progress_callback(progress, f"Processing page {page_index + 1} of {total_pages}...")

        if progress_callback:
            progress_callback(100, "Finalizing...")

        new_doc.save(output_path)
        return True

    except Exception as e:
        if log_callback:
            log_callback(f"Critical error: {str(e)}")
            import traceback

            log_callback(traceback.format_exc())
        return False

    finally:
        if new_doc is not None:
            new_doc.close()
        if doc is not None:
            doc.close()


class CustomDialog:
    """Custom dialog window with app branding instead of Python icon."""

    def __init__(self, parent, title, message, dialog_type="info", details="", on_confirm=None):
        self.parent = parent
        self.title_text = title
        self.message = message
        self.dialog_type = dialog_type
        self.details = details
        self.on_confirm = on_confirm
        self.result = False

        self.clr_bg = "#0b1020"
        self.clr_accent = "#22c55e"
        self.clr_accent_hover = "#34d399"
        self.clr_accent_blue = "#7db3ff"
        self.clr_border = "#2b364f"
        self.clr_text = "#e5edf7"
        self.clr_muted = "#94a3b8"

        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.dialog_width = 720 if details else 450
        self.dialog_height = 520 if details else 320
        self.top.geometry(f"{self.dialog_width}x{self.dialog_height}")
        self.top.resizable(False, False)
        self.top.configure(bg=self.clr_bg)
        self.top.transient(parent)
        self.top.grab_set()

        self.top.update_idletasks()
        x = (self.top.winfo_screenwidth() // 2) - (self.dialog_width // 2)
        y = (self.top.winfo_screenheight() // 2) - (self.dialog_height // 2)
        self.top.geometry(f"{self.dialog_width}x{self.dialog_height}+{x}+{y}")

        self._create_widgets()
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)
        parent.wait_window(self.top)

    def _create_widgets(self):
        main_frame = tk.Frame(self.top, bg=self.clr_bg, padx=30, pady=25)
        main_frame.pack(fill=tk.BOTH, expand=True)

        icon_label = tk.Label(
            main_frame,
            text="[PDF]",
            font=("Segoe UI", 28, "bold"),
            bg=self.clr_bg,
            fg=self.clr_accent_blue,
        )
        icon_label.pack(pady=(0, 15))

        title_label = tk.Label(
            main_frame,
            text=self.title_text,
            font=("Segoe UI", 14, "bold"),
            bg=self.clr_bg,
            fg=self.clr_accent_blue,
        )
        title_label.pack(pady=(0, 10))

        msg_label = tk.Label(
            main_frame,
            text=self.message,
            font=("Segoe UI", 10),
            bg=self.clr_bg,
            fg=self.clr_text,
            wraplength=self.dialog_width - 90,
            justify=tk.CENTER,
        )
        msg_label.pack(pady=(0, 15))

        if self.details:
            details_frame = tk.Frame(
                main_frame,
                bg="#000000",
                highlightbackground=self.clr_border,
                highlightthickness=1,
            )
            details_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 14))

            details_text = tk.Text(
                details_frame,
                height=6,
                font=("Consolas", 9),
                bg="#000000",
                fg="#7CFF9B",
                relief=tk.FLAT,
                padx=10,
                pady=10,
                wrap=tk.NONE,
            )
            details_scroll_y = ttk.Scrollbar(details_frame, orient=tk.VERTICAL, command=details_text.yview)
            details_scroll_x = ttk.Scrollbar(details_frame, orient=tk.HORIZONTAL, command=details_text.xview)
            details_text.configure(
                yscrollcommand=details_scroll_y.set,
                xscrollcommand=details_scroll_x.set,
            )
            details_text.grid(row=0, column=0, sticky="nsew")
            details_scroll_y.grid(row=0, column=1, sticky="ns")
            details_scroll_x.grid(row=1, column=0, sticky="ew")
            details_frame.grid_rowconfigure(0, weight=1)
            details_frame.grid_columnconfigure(0, weight=1)
            details_text.insert(tk.END, self.details)
            details_text.config(state=tk.DISABLED)

        btn_frame = tk.Frame(main_frame, bg=self.clr_bg)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        if self.dialog_type == "confirm":
            no_btn = tk.Button(
                btn_frame,
                text="No",
                font=("Segoe UI", 10, "bold"),
                bg="#cbd5e1",
                fg="#000000",
                activebackground="#e2e8f0",
                activeforeground="#000000",
                relief=tk.FLAT,
                padx=30,
                pady=10,
                cursor="hand2",
                command=self._on_no,
            )
            no_btn.pack(side=tk.LEFT, expand=True, padx=(0, 10))
            no_btn.bind("<Enter>", lambda e: no_btn.config(bg="#e2e8f0"))
            no_btn.bind("<Leave>", lambda e: no_btn.config(bg="#cbd5e1"))

            yes_btn = tk.Button(
                btn_frame,
                text="Yes",
                font=("Segoe UI", 10, "bold"),
                bg=self.clr_accent,
                fg="#000000",
                activebackground=self.clr_accent_hover,
                activeforeground="#000000",
                relief=tk.FLAT,
                padx=30,
                pady=10,
                cursor="hand2",
                command=self._on_yes,
            )
            yes_btn.pack(side=tk.RIGHT, expand=True, padx=(10, 0))
            yes_btn.bind("<Enter>", lambda e: yes_btn.config(bg=self.clr_accent_hover))
            yes_btn.bind("<Leave>", lambda e: yes_btn.config(bg=self.clr_accent))
        else:
            ok_btn = tk.Button(
                btn_frame,
                text="Close" if self.details else "OK",
                font=("Segoe UI", 11, "bold"),
                bg=self.clr_accent,
                fg="#04110a",
                relief=tk.FLAT,
                padx=60,
                pady=12,
                cursor="hand2",
                command=self._on_ok,
            )
            ok_btn.pack(side=tk.LEFT, expand=True)
            ok_btn.bind("<Enter>", lambda e: ok_btn.config(bg=self.clr_accent_hover))
            ok_btn.bind("<Leave>", lambda e: ok_btn.config(bg=self.clr_accent))

    def _on_ok(self):
        self.result = True
        if self.on_confirm:
            self.on_confirm()
        self.top.destroy()

    def _on_yes(self):
        self.result = True
        if self.on_confirm:
            self.on_confirm()
        self.top.destroy()

    def _on_no(self):
        self.result = False
        self.top.destroy()

    def _on_close(self):
        self.result = False
        self.top.destroy()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Edge Bleed - Made by Douglas C.")
        self.root.geometry("820x1080")
        self.root.resizable(False, False)

        self.clr_bg = "#0b1020"
        self.clr_card = "#111827"
        self.clr_card_2 = "#7db3ff"
        self.clr_button_hover = "#a7caff"
        self.clr_input = "#070b14"
        self.clr_accent = "#4f8cff"
        self.clr_accent_hover = "#6aa0ff"
        self.clr_success = "#22c55e"
        self.clr_success_hover = "#34d399"
        self.clr_text = "#e5edf7"
        self.clr_muted = "#94a3b8"
        self.clr_border = "#2b364f"
        self.clr_log_green = "#7CFF9B"
        self.clr_accent_blue = "#7db3ff"

        self.root.configure(bg=self.clr_bg)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Modern.Horizontal.TScale",
            background=self.clr_card,
            troughcolor="#26324a",
            bordercolor=self.clr_border,
            lightcolor=self.clr_accent,
            darkcolor=self.clr_accent,
        )
        style.configure(
            "Modern.Horizontal.TProgressbar",
            background=self.clr_success,
            troughcolor="#1d273b",
            borderwidth=0,
            lightcolor=self.clr_success,
            darkcolor=self.clr_success,
        )
        style.configure(
            "Modern.Vertical.TScrollbar",
            background=self.clr_card_2,
            troughcolor=self.clr_input,
            bordercolor=self.clr_border,
            arrowcolor=self.clr_text,
        )

        def hover(button, normal, active):
            button.bind("<Enter>", lambda _e: button.config(bg=active))
            button.bind("<Leave>", lambda _e: button.config(bg=normal))

        main_frame = tk.Frame(root, bg=self.clr_bg, padx=34, pady=28)
        main_frame.pack(fill=tk.BOTH, expand=True)

        header_frame = tk.Frame(
            main_frame,
            bg=self.clr_card,
            padx=28,
            pady=16,
            highlightbackground=self.clr_border,
            highlightthickness=1,
        )
        header_frame.pack(fill=tk.X, pady=(0, 18))

        title_left_frame = tk.Frame(header_frame, bg=self.clr_card)
        title_left_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            title_left_frame,
            text="PDF Edge Bleed",
            font=("Segoe UI", 30, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
        ).pack(anchor=tk.W)

        tk.Label(
            title_left_frame,
            text="Mirror bleed generator",
            font=("Segoe UI", 11),
            bg=self.clr_card,
            fg=self.clr_muted,
        ).pack(anchor=tk.W, pady=(5, 0))

        tk.Label(
            header_frame,
            text="V1.3\nMade by Douglas C.",
            font=("Segoe UI", 10, "bold"),
            justify=tk.RIGHT,
            bg=self.clr_card,
            fg=self.clr_accent_blue,
        ).pack(side=tk.RIGHT, anchor=tk.NE)

        settings_card = tk.Frame(
            main_frame,
            bg=self.clr_card,
            padx=28,
            pady=22,
            highlightbackground=self.clr_border,
            highlightthickness=1,
            relief=tk.FLAT,
        )
        settings_card.pack(fill=tk.X, pady=(0, 12))

        self.edge_sample_inches = 0.05

        tk.Label(
            settings_card,
            text="Bleed Controls",
            font=("Segoe UI", 15, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
        ).pack(anchor=tk.W)

        controls_grid = tk.Frame(settings_card, bg=self.clr_card)
        controls_grid.pack(fill=tk.X, pady=(12, 0))
        controls_grid.grid_columnconfigure(1, weight=1)

        tk.Label(
            controls_grid,
            text="White edge skip top",
            font=("Segoe UI", 10, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
        ).grid(row=0, column=0, sticky="w", padx=(0, 16), pady=(0, 10))

        self.edge_inset_vars = {}
        self.inset_labels = {}

        for row, (side_key, side_label) in enumerate(
            (
                ("top", "White edge skip top"),
                ("bottom", "White edge skip bottom"),
                ("left", "White edge skip left"),
                ("right", "White edge skip right"),
            )
        ):
            if row > 0:
                tk.Label(
                    controls_grid,
                    text=side_label,
                    font=("Segoe UI", 10, "bold"),
                    bg=self.clr_card,
                    fg=self.clr_text,
                ).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=(0, 10))

            self.edge_inset_vars[side_key] = tk.DoubleVar(value=0)
            inset_scale = ttk.Scale(
                controls_grid,
                variable=self.edge_inset_vars[side_key],
                from_=0,
                to=80,
                orient=tk.HORIZONTAL,
                style="Modern.Horizontal.TScale",
            )
            inset_scale.grid(row=row, column=1, sticky="ew", pady=(0, 10))

            self.inset_labels[side_key] = tk.Label(
                controls_grid,
                text="0 px",
                font=("Consolas", 10, "bold"),
                bg=self.clr_card_2,
                fg="#000000",
                padx=12,
                pady=6,
            )
            self.inset_labels[side_key].grid(row=row, column=2, sticky="e", padx=(16, 0), pady=(0, 10))
            inset_scale.config(command=lambda val, key=side_key: update_inset_label(key, val))

        tk.Label(
            controls_grid,
            text="Resolution:",
            font=("Segoe UI", 10, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
        ).grid(row=4, column=0, sticky="w", padx=(0, 16))

        self.bleed_dpi_var = tk.DoubleVar(value=360)
        dpi_scale = ttk.Scale(
            controls_grid,
            variable=self.bleed_dpi_var,
            from_=300,
            to=600,
            orient=tk.HORIZONTAL,
            style="Modern.Horizontal.TScale",
        )
        dpi_scale.grid(row=4, column=1, sticky="ew")

        self.dpi_label = tk.Label(
            controls_grid,
            text="360 DPI",
            font=("Consolas", 10, "bold"),
            bg=self.clr_card_2,
            fg="#000000",
            padx=12,
            pady=6,
        )
        self.dpi_label.grid(row=4, column=2, sticky="e", padx=(16, 0))

        self.flatten_page_var = tk.BooleanVar(value=False)
        flatten_check = tk.Checkbutton(
            controls_grid,
            text="Flatten transparency mode",
            variable=self.flatten_page_var,
            font=("Segoe UI", 10, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
            activebackground=self.clr_card,
            activeforeground=self.clr_text,
            selectcolor=self.clr_input,
            relief=tk.FLAT,
            cursor="hand2",
        )
        flatten_check.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def refresh_slider_labels():
            dpi = int(self.bleed_dpi_var.get())
            for side_key, inset_var in self.edge_inset_vars.items():
                inset_px = int(round(inset_var.get()))
                self.inset_labels[side_key].config(text=f"{inset_px} px")
            self.dpi_label.config(text=f"{dpi} DPI")

        def update_inset_label(side_key, val):
            self.edge_inset_vars[side_key].set(int(round(float(val))))
            refresh_slider_labels()

        def update_dpi_label(val):
            dpi = int(float(val))
            dpi = round(dpi / 30) * 30
            dpi = max(300, min(600, dpi))
            self.bleed_dpi_var.set(dpi)
            refresh_slider_labels()

        dpi_scale.config(command=update_dpi_label)
        refresh_slider_labels()

        card = tk.Frame(
            main_frame,
            bg=self.clr_card,
            padx=28,
            pady=16,
            highlightbackground=self.clr_border,
            highlightthickness=1,
            relief=tk.FLAT,
        )
        card.pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            card,
            text="Files",
            font=("Segoe UI", 15, "bold"),
            bg=self.clr_card,
            fg=self.clr_text,
        ).pack(anchor=tk.W, pady=(0, 12))

        self.input_file = tk.StringVar()
        tk.Label(
            card,
            text="Original PDF file",
            font=("Segoe UI", 9, "bold"),
            bg=self.clr_card,
            fg=self.clr_muted,
        ).pack(anchor=tk.W)

        in_frame = tk.Frame(card, bg=self.clr_card)
        in_frame.pack(fill=tk.X, pady=(8, 16))

        tk.Entry(
            in_frame,
            textvariable=self.input_file,
            font=("Consolas", 10),
            bg=self.clr_input,
            fg=self.clr_text,
            insertbackground=self.clr_text,
            bd=0,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=12, padx=(0, 10))

        browse_input_btn = tk.Button(
            in_frame,
            text="Browse",
            command=self.browse_input,
            bg=self.clr_card_2,
            fg="#000000",
            relief=tk.FLAT,
            padx=22,
            pady=10,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            activebackground=self.clr_button_hover,
            activeforeground="#000000",
        )
        browse_input_btn.pack(side=tk.RIGHT)
        hover(browse_input_btn, self.clr_card_2, self.clr_button_hover)

        self.output_file = tk.StringVar()
        tk.Label(
            card,
            text="Save as",
            font=("Segoe UI", 9, "bold"),
            bg=self.clr_card,
            fg=self.clr_muted,
        ).pack(anchor=tk.W)

        out_frame = tk.Frame(card, bg=self.clr_card)
        out_frame.pack(fill=tk.X, pady=(8, 0))

        tk.Entry(
            out_frame,
            textvariable=self.output_file,
            font=("Consolas", 10),
            bg=self.clr_input,
            fg=self.clr_text,
            insertbackground=self.clr_text,
            bd=0,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=12, padx=(0, 10))

        browse_output_btn = tk.Button(
            out_frame,
            text="Browse",
            command=self.browse_output,
            bg=self.clr_card_2,
            fg="#000000",
            relief=tk.FLAT,
            padx=22,
            pady=10,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            activebackground=self.clr_button_hover,
            activeforeground="#000000",
        )
        browse_output_btn.pack(side=tk.RIGHT)
        hover(browse_output_btn, self.clr_card_2, self.clr_button_hover)

        self.btn_run = tk.Button(
            main_frame,
            text="GENERATE PDF WITH BLEED",
            font=("Segoe UI", 14, "bold"),
            bg=self.clr_success,
            fg="#04110a",
            activebackground=self.clr_success_hover,
            activeforeground="#04110a",
            relief=tk.FLAT,
            pady=16,
            cursor="hand2",
            command=self.start_process,
        )
        self.btn_run.pack(fill=tk.X, pady=(0, 14))
        hover(self.btn_run, self.clr_success, self.clr_success_hover)

        self.progress_var = tk.DoubleVar()

        self.progress = ttk.Progressbar(
            main_frame,
            variable=self.progress_var,
            mode="determinate",
            length=600,
            style="Modern.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X, pady=(0, 6))

        self.progress_label = tk.Label(
            main_frame,
            text="",
            font=("Consolas", 10),
            bg=self.clr_bg,
            fg=self.clr_muted,
        )
        self.progress_label.pack(anchor=tk.W, pady=(0, 12))

        log_frame = tk.Frame(
            main_frame,
            bg=self.clr_input,
            highlightbackground=self.clr_border,
            highlightthickness=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            log_frame,
            text="Process Log",
            font=("Segoe UI", 10, "bold"),
            bg=self.clr_input,
            fg=self.clr_muted,
            padx=18,
            pady=10,
        ).pack(anchor=tk.W)

        self.log = tk.Text(
            log_frame,
            height=24,
            font=("Consolas", 9),
            state=tk.DISABLED,
            bg=self.clr_input,
            fg=self.clr_log_green,
            relief=tk.FLAT,
            padx=18,
            pady=12,
            wrap=tk.WORD,
        )
        self.log.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview, style="Modern.Vertical.TScrollbar")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.config(yscrollcommand=scrollbar.set)

        self.processing = False
        self.last_edge_insets = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        self.last_flatten_page = False

    def browse_input(self):
        f = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if f:
            self.input_file.set(f)
            base = os.path.splitext(f)[0]
            self.output_file.set(f"{base}_EDGE_BLEED.pdf")
            self.write_log(f"File selected: {os.path.basename(f)}")

    def browse_output(self):
        f = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if f:
            self.output_file.set(f)
            self.write_log(f"Destination set: {os.path.basename(f)}")

    def write_log(self, text):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, f"{text}\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def write_log_threadsafe(self, text):
        self.root.after(0, lambda: self.write_log(text))

    def update_progress(self, value, label=""):
        self.progress_var.set(value)
        if label:
            self.progress_label.config(text=label)

    def open_file(self, filepath):
        try:
            if sys.platform == "darwin":
                subprocess.call(["open", filepath])
            elif sys.platform == "win32":
                os.startfile(filepath)
            elif sys.platform == "linux":
                subprocess.call(["xdg-open", filepath])
        except Exception as e:
            self.write_log(f"Could not open file: {str(e)}")

    def show_success(self, title, message, details=""):
        dialog = CustomDialog(self.root, title, message, "info", details)
        return dialog.result

    def show_error(self, title, message, details=""):
        dialog = CustomDialog(self.root, title, message, "error", details)
        return dialog.result

    def show_confirm(self, title, message, details="", on_confirm=None):
        dialog = CustomDialog(self.root, title, message, "confirm", details, on_confirm)
        return dialog.result

    def show_warning(self, title, message):
        dialog = CustomDialog(self.root, title, message, "warning")
        return dialog.result

    def start_process(self):
        if self.processing:
            self.show_warning("Warning", "A process is already running.")
            return

        input_path = self.input_file.get().strip()
        output_path = self.output_file.get().strip()

        if not input_path or not output_path:
            self.show_warning("Warning", "Please select input and output files.")
            return

        if not os.path.exists(input_path):
            self.show_error("Error", "The input file does not exist.")
            return

        if input_path == output_path:
            self.show_error("Error", "Output file must be different from input file.")
            return

        self.processing = True
        self.btn_run.config(
            state=tk.DISABLED,
            bg=self.clr_border,
            fg=self.clr_muted,
            text="PROCESSING... DO NOT CLOSE WINDOW",
        )
        self.progress_var.set(0)
        self.progress_label.config(text="Initializing...")

        edge_sample = self.edge_sample_inches
        bleed_dpi = int(self.bleed_dpi_var.get())
        flatten_page = bool(self.flatten_page_var.get())
        edge_inset_px = {
            side_key: int(round(inset_var.get()))
            for side_key, inset_var in self.edge_inset_vars.items()
        }
        self.last_edge_insets = edge_inset_px.copy()
        self.last_flatten_page = flatten_page
        edge_skip_summary = (
            f"Top {edge_inset_px['top']}px | Bottom {edge_inset_px['bottom']}px | "
            f"Left {edge_inset_px['left']}px | Right {edge_inset_px['right']}px"
        )

        self.write_log("")
        self.write_log("=" * 60)
        self.write_log(f"Input: {os.path.basename(input_path)}")
        self.write_log(f"Output: {os.path.basename(output_path)}")
        self.write_log("Bleed: 0.125\" (9 points)")
        self.write_log(f"Internal Edge Sample: {edge_sample:.2f}\"")
        self.write_log(f"White Edge Skip: {edge_skip_summary}")
        self.write_log(f"Bleed DPI: {bleed_dpi}")
        self.write_log(f"Flatten Transparency Mode: {'ON' if flatten_page else 'OFF'}")
        self.write_log("Method: Edge Extension with inset sampling")
        self.write_log("=" * 60)

        thread = threading.Thread(
            target=self.run_logic,
            args=(input_path, output_path, edge_sample, bleed_dpi, edge_inset_px, flatten_page),
            daemon=True,
        )
        thread.start()

    def run_logic(self, i, o, edge_sample, bleed_dpi, edge_inset_px, flatten_page):
        def progress_callback(value, label):
            self.root.after(0, lambda: self.update_progress(value, label))

        success = create_mirror_bleed(
            i,
            o,
            self.write_log_threadsafe,
            progress_callback,
            edge_sample_inches=edge_sample,
            bleed_dpi=bleed_dpi,
            edge_inset_px=edge_inset_px,
            flatten_page=flatten_page,
        )
        self.root.after(0, lambda: self.finish(success))

    def finish(self, success):
        self.processing = False
        self.progress_var.set(100)

        if success:
            self.progress_label.config(text="Completed - 100%")
            self.write_log("")
            self.write_log("=" * 60)
            self.write_log("PROCESS COMPLETED SUCCESSFULLY")
            self.write_log("=" * 60)
            self.write_log(f"File saved at: {self.output_file.get()}")
            edge_skip_summary = (
                f"Top {self.last_edge_insets['top']}px | Bottom {self.last_edge_insets['bottom']}px | "
                f"Left {self.last_edge_insets['left']}px | Right {self.last_edge_insets['right']}px"
            )
            flatten_summary = "ON" if self.last_flatten_page else "OFF"

            self.show_success(
                "Success",
                "PDF with edge-extended bleed generated successfully.\n\n"
                "Vector content preserved\n"
                f"{int(self.bleed_dpi_var.get())} DPI bleed quality\n"
                "Bleed color matched to rendered PDF appearance\n"
                f"White edge skip applied: {edge_skip_summary}\n"
                f"Flatten transparency mode: {flatten_summary}\n"
                "Ready for professional print",
                f"File saved at: {self.output_file.get()}",
            )

            if self.show_confirm("Open file", "Do you want to open the resulting PDF?"):
                self.open_file(self.output_file.get())
        else:
            self.progress_label.config(text="Failed")
            self.write_log("")
            self.write_log("PROCESS FAILED - Check log for details")
            self.show_error(
                "Error",
                "An error occurred during processing.\nCheck the log for more details.",
            )

        self.btn_run.config(
            state=tk.NORMAL,
            bg=self.clr_success,
            fg="#04110a",
            text="GENERATE PDF WITH BLEED",
        )


if __name__ == "__main__":
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = App(root)
    root.mainloop()
