import fitz
import os
import sys
import subprocess
import customtkinter as ctk
from tkinter import filedialog
import threading
from PIL import Image
import io
import math

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def detect_colorspace(input_path):
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
    except Exception:
        return "CMYK"


def clamp_crop_box(left, top, right, bottom, image_width, image_height):
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
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "bottom":
                    return crop_resize(
                        (0, page_h_px - inset_bottom - sample_px, page_w_px, page_h_px - inset_bottom),
                        (page_w_px, bleed_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "left":
                    return crop_resize(
                        (inset_left, 0, inset_left + sample_px, page_h_px),
                        (bleed_px, page_h_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "right":
                    return crop_resize(
                        (page_w_px - inset_right - sample_px, 0, page_w_px - inset_right, page_h_px),
                        (bleed_px, page_h_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "tl":
                    return crop_resize(
                        (inset_left, inset_top, inset_left + sample_px, inset_top + sample_px),
                        (bleed_px, bleed_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "tr":
                    return crop_resize(
                        (page_w_px - inset_right - sample_px, inset_top, page_w_px - inset_right, inset_top + sample_px),
                        (bleed_px, bleed_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "bl":
                    return crop_resize(
                        (inset_left, page_h_px - inset_bottom - sample_px, inset_left + sample_px, page_h_px - inset_bottom),
                        (bleed_px, bleed_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                if side == "br":
                    return crop_resize(
                        (page_w_px - inset_right - sample_px, page_h_px - inset_bottom - sample_px, page_w_px - inset_right, page_h_px - inset_bottom),
                        (bleed_px, bleed_px),
                        flip_h=flip_h, flip_v=flip_v,
                    )
                raise ValueError(f"Unknown bleed side: {side}")

            new_page.insert_image(
                fitz.Rect(bleed_pts, -outer_bleed_overlap_pts, w + bleed_pts, bleed_pts + seam_cover_pts["top"]),
                stream=create_bleed_image("top", flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(bleed_pts, h + bleed_pts - seam_cover_pts["bottom"], w + bleed_pts, new_h + outer_bleed_overlap_pts),
                stream=create_bleed_image("bottom", flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(-outer_bleed_overlap_pts, bleed_pts, bleed_pts + seam_cover_pts["left"], h + bleed_pts),
                stream=create_bleed_image("left", flip_h=True),
            )
            new_page.insert_image(
                fitz.Rect(w + bleed_pts - seam_cover_pts["right"], bleed_pts, new_w + outer_bleed_overlap_pts, h + bleed_pts),
                stream=create_bleed_image("right", flip_h=True),
            )
            new_page.insert_image(
                fitz.Rect(-outer_bleed_overlap_pts, -outer_bleed_overlap_pts, bleed_pts + seam_cover_pts["left"], bleed_pts + seam_cover_pts["top"]),
                stream=create_bleed_image("tl", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(w + bleed_pts - seam_cover_pts["right"], -outer_bleed_overlap_pts, new_w + outer_bleed_overlap_pts, bleed_pts + seam_cover_pts["top"]),
                stream=create_bleed_image("tr", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(-outer_bleed_overlap_pts, h + bleed_pts - seam_cover_pts["bottom"], bleed_pts + seam_cover_pts["left"], new_h + outer_bleed_overlap_pts),
                stream=create_bleed_image("bl", flip_h=True, flip_v=True),
            )
            new_page.insert_image(
                fitz.Rect(w + bleed_pts - seam_cover_pts["right"], h + bleed_pts - seam_cover_pts["bottom"], new_w + outer_bleed_overlap_pts, new_h + outer_bleed_overlap_pts),
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
    def __init__(self, parent, title, message, dialog_type="info", details="", on_confirm=None):
        self.parent = parent
        self.title_text = title
        self.message = message
        self.dialog_type = dialog_type
        self.details = details
        self.on_confirm = on_confirm
        self.result = False
        self.palette = parent.palette if hasattr(parent, 'palette') else {}
        c = self.palette

        self.top = ctk.CTkToplevel(parent)
        self.top.title(title)
        self.dialog_width = 720 if details else 450
        self.dialog_height = 520 if details else 320
        self.top.geometry(f"{self.dialog_width}x{self.dialog_height}")
        self.top.resizable(False, False)
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
        c = self.palette
        main_frame = ctk.CTkFrame(self.top, fg_color=c.get("bg", "#F5F7FA"), corner_radius=0)
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=30, pady=25)

        icon_label = ctk.CTkLabel(
            main_frame, text="[PDF]",
            font=("Segoe UI", 28, "bold"),
            text_color=c.get("primary", "#3B82F6"),
        )
        icon_label.pack(pady=(0, 15))

        title_label = ctk.CTkLabel(
            main_frame, text=self.title_text,
            font=("Segoe UI", 14, "bold"),
            text_color=c.get("primary", "#3B82F6"),
        )
        title_label.pack(pady=(0, 10))

        msg_label = ctk.CTkLabel(
            main_frame, text=self.message,
            font=("Segoe UI", 10),
            text_color=c.get("text", "#334155"),
            wraplength=self.dialog_width - 90,
            justify="center",
        )
        msg_label.pack(pady=(0, 15))

        if self.details:
            details_frame = ctk.CTkFrame(main_frame, corner_radius=8, border_width=1)
            details_frame.pack(fill=ctk.BOTH, expand=True, pady=(0, 14))

            details_text = ctk.CTkTextbox(
                details_frame, font=("Consolas", 9),
                wrap="none",
            )
            details_text.pack(fill=ctk.BOTH, expand=True, padx=0, pady=0)
            details_text.insert("0.0", self.details)
            details_text.configure(state="disabled")

        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, pady=(10, 0))

        if self.dialog_type == "confirm":
            no_btn = ctk.CTkButton(
                btn_frame, text="No",
                font=("Segoe UI", 10, "bold"),
                fg_color="#cbd5e1", hover_color="#e2e8f0",
                text_color="#000000",
                corner_radius=8, cursor="hand2",
                command=self._on_no,
            )
            no_btn.pack(side=ctk.LEFT, expand=True, padx=(0, 10))

            yes_btn = ctk.CTkButton(
                btn_frame, text="Yes",
                font=("Segoe UI", 10, "bold"),
                fg_color=c.get("primary", "#3B82F6"),
                hover_color=c.get("primary_hover", "#2563EB"),
                text_color="#FFFFFF",
                corner_radius=8, cursor="hand2",
                command=self._on_yes,
            )
            yes_btn.pack(side=ctk.RIGHT, expand=True, padx=(10, 0))
        else:
            ok_btn = ctk.CTkButton(
                btn_frame, text="Close" if self.details else "OK",
                font=("Segoe UI", 11, "bold"),
                fg_color=c.get("primary", "#3B82F6"),
                hover_color=c.get("primary_hover", "#2563EB"),
                text_color="#FFFFFF",
                corner_radius=8, cursor="hand2",
                command=self._on_ok,
            )
            ok_btn.pack(side=ctk.LEFT, expand=True)

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

        self.processing = False
        self.dark_mode = False
        self.edge_sample_inches = 0.05

        self.palettes = {
            "light": {
                "bg": "#F5F7FA",
                "card": "#FFFFFF",
                "card_border": "#E2E8F0",
                "text": "#334155",
                "muted": "#94A3B8",
                "label": "#475569",
                "input_bg": "#F1F5F9",
                "input_border": "#CBD5E1",
                "primary": "#3B82F6",
                "primary_hover": "#2563EB",
                "accent": "#60A5FA",
                "success": "#10B981",
                "log": "#64748B",
            },
            "dark": {
                "bg": "#0F172A",
                "card": "#1E293B",
                "card_border": "#334155",
                "text": "#F1F5F9",
                "muted": "#94A3B8",
                "label": "#CBD5E1",
                "input_bg": "#0F172A",
                "input_border": "#334155",
                "primary": "#3B82F6",
                "primary_hover": "#60A5FA",
                "accent": "#60A5FA",
                "success": "#34D399",
                "log": "#94A3B8",
            },
        }

        self.palette = dict(self.palettes["light"])
        self._build_ui()
        self._apply_colors()

    def _build_ui(self):
        self.main_frame = ctk.CTkFrame(self.root, corner_radius=0)
        self.main_frame.pack(fill=ctk.BOTH, expand=True, padx=28, pady=24)

        self._build_header()
        self._build_settings()
        self._build_files()
        self._build_button()
        self._build_progress()
        self._build_log()

    def _build_header(self):
        self.header_frame = ctk.CTkFrame(
            self.main_frame, corner_radius=12, border_width=1,
        )
        self.header_frame.pack(fill=ctk.X, pady=(0, 16))
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            self.header_frame, text="PDF Edge Bleed",
            font=("Segoe UI", 26, "bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=24, pady=(18, 0))

        self.subtitle_label = ctk.CTkLabel(
            self.header_frame, text="Mirror bleed generator",
            font=("Segoe UI", 11),
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w", padx=24, pady=(0, 4))

        self.version_label = ctk.CTkLabel(
            self.header_frame, text="V2.0\nMade by Douglas C.",
            font=("Segoe UI", 10, "bold"), justify="right",
        )
        self.version_label.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(0, 24), pady=(18, 4))

    def _build_settings(self):
        self.settings_card = ctk.CTkFrame(
            self.main_frame, corner_radius=12, border_width=1,
        )
        self.settings_card.pack(fill=ctk.X, pady=(0, 12))

        top_row = ctk.CTkFrame(self.settings_card, corner_radius=0, fg_color="transparent")
        top_row.pack(fill=ctk.X, padx=24, pady=(20, 0))

        self.config_label = ctk.CTkLabel(
            top_row, text="Bleed Controls",
            font=("Segoe UI", 16, "bold"), anchor="w",
        )
        self.config_label.pack(side=ctk.LEFT)

        self.theme_switch = ctk.CTkSwitch(
            top_row, text="", command=self._toggle_theme,
            progress_color="#3B82F6", corner_radius=12,
            switch_width=40, switch_height=22,
        )
        self.theme_switch.pack(side=ctk.RIGHT)

        self.theme_label = ctk.CTkLabel(
            top_row, text="\u2600\ufe0f", font=("Segoe UI", 13),
        )
        self.theme_label.pack(side=ctk.RIGHT, padx=(0, 6))

        self.grid_frame = ctk.CTkFrame(self.settings_card, corner_radius=0, fg_color="transparent")
        self.grid_frame.pack(fill=ctk.X, padx=24, pady=(16, 20))
        self.grid_frame.grid_columnconfigure(1, weight=1)

        self.edge_inset_vars = {}
        self.inset_labels = {}

        for row, (side_key, side_label) in enumerate([
            ("top", "White edge skip top"),
            ("bottom", "White edge skip bottom"),
            ("left", "White edge skip left"),
            ("right", "White edge skip right"),
        ]):
            lbl = ctk.CTkLabel(
                self.grid_frame, text=side_label,
                font=("Segoe UI", 11, "bold"),
            )
            lbl.grid(row=row, column=0, sticky="w", pady=(0, 10))

            self.edge_inset_vars[side_key] = ctk.DoubleVar(value=0)
            inset_slider = ctk.CTkSlider(
                self.grid_frame,
                variable=self.edge_inset_vars[side_key],
                from_=0, to=80, number_of_steps=80,
                orientation="horizontal",
                command=lambda val, key=side_key: self._update_inset_label(key),
            )
            inset_slider.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=(0, 10))

            self.inset_labels[side_key] = ctk.CTkLabel(
                self.grid_frame, text="0 px",
                font=("Consolas", 10, "bold"),
            )
            self.inset_labels[side_key].grid(row=row, column=2, sticky="e", padx=(16, 0), pady=(0, 10))

        dpi_lbl = ctk.CTkLabel(
            self.grid_frame, text="Resolution:",
            font=("Segoe UI", 11, "bold"),
        )
        dpi_lbl.grid(row=4, column=0, sticky="w", pady=(0, 10))

        self.bleed_dpi_var = ctk.DoubleVar(value=360)
        self.dpi_slider = ctk.CTkSlider(
            self.grid_frame,
            variable=self.bleed_dpi_var,
            from_=300, to=600,
            orientation="horizontal",
            command=self._update_dpi_label,
        )
        self.dpi_slider.grid(row=4, column=1, sticky="ew", padx=(14, 0), pady=(0, 10))

        self.dpi_label = ctk.CTkLabel(
            self.grid_frame, text="360 DPI",
            font=("Consolas", 10, "bold"),
        )
        self.dpi_label.grid(row=4, column=2, sticky="e", padx=(16, 0), pady=(0, 10))

        self.flatten_page_var = ctk.BooleanVar(value=False)
        self.flatten_check = ctk.CTkCheckBox(
            self.grid_frame, text="Flatten transparency mode",
            variable=self.flatten_page_var,
            font=("Segoe UI", 11, "bold"),
            corner_radius=6,
            cursor="hand2",
        )
        self.flatten_check.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self._refresh_slider_labels()

    def _build_files(self):
        self.files_card = ctk.CTkFrame(
            self.main_frame, corner_radius=12, border_width=1,
        )
        self.files_card.pack(fill=ctk.X, pady=(0, 12))

        self.files_title = ctk.CTkLabel(
            self.files_card, text="Files",
            font=("Segoe UI", 16, "bold"),
        )
        self.files_title.pack(anchor="w", padx=24, pady=(20, 12))

        self.input_file = ctk.StringVar()

        input_label = ctk.CTkLabel(
            self.files_card, text="Original PDF file",
            font=("Segoe UI", 10, "bold"),
        )
        input_label.pack(anchor="w", padx=24)

        in_frame = ctk.CTkFrame(self.files_card, fg_color="transparent")
        in_frame.pack(fill=ctk.X, padx=24, pady=(8, 16))

        self.input_entry = ctk.CTkEntry(
            in_frame, textvariable=self.input_file,
            font=("Consolas", 10), corner_radius=8, height=36,
        )
        self.input_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 10))

        self.browse_input_btn = ctk.CTkButton(
            in_frame, text="Browse",
            command=self.browse_input,
            font=("Segoe UI", 10, "bold"),
            corner_radius=8, height=36, cursor="hand2",
        )
        self.browse_input_btn.pack(side=ctk.RIGHT)

        self.output_file = ctk.StringVar()

        output_label = ctk.CTkLabel(
            self.files_card, text="Save as",
            font=("Segoe UI", 10, "bold"),
        )
        output_label.pack(anchor="w", padx=24)

        out_frame = ctk.CTkFrame(self.files_card, fg_color="transparent")
        out_frame.pack(fill=ctk.X, padx=24, pady=(8, 20))

        self.output_entry = ctk.CTkEntry(
            out_frame, textvariable=self.output_file,
            font=("Consolas", 10), corner_radius=8, height=36,
        )
        self.output_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 10))

        self.browse_output_btn = ctk.CTkButton(
            out_frame, text="Browse",
            command=self.browse_output,
            font=("Segoe UI", 10, "bold"),
            corner_radius=8, height=36, cursor="hand2",
        )
        self.browse_output_btn.pack(side=ctk.RIGHT)

    def _build_button(self):
        self.btn_run = ctk.CTkButton(
            self.main_frame, text="GENERATE PDF WITH BLEED",
            font=("Segoe UI", 14, "bold"),
            corner_radius=10, height=50,
            cursor="hand2", command=self.start_process,
        )
        self.btn_run.pack(fill=ctk.X, pady=(12, 0))

    def _build_progress(self):
        self.progress = ctk.CTkProgressBar(
            self.main_frame,
            orientation="horizontal",
            corner_radius=6,
        )
        self.progress.pack(fill=ctk.X, pady=(10, 4))
        self.progress.set(0)

        self.progress_label = ctk.CTkLabel(
            self.main_frame, text="",
            font=("Consolas", 10),
        )
        self.progress_label.pack(anchor="w", padx=4, pady=(0, 10))

    def _build_log(self):
        self.log_frame = ctk.CTkFrame(
            self.main_frame, corner_radius=12, border_width=1,
        )
        self.log_frame.pack(fill=ctk.BOTH, expand=True)

        log_header = ctk.CTkLabel(
            self.log_frame, text="Process Log",
            font=("Segoe UI", 10, "bold"),
        )
        log_header.pack(anchor="w", padx=24, pady=(14, 6))

        self.log = ctk.CTkTextbox(
            self.log_frame, font=("Consolas", 9),
            corner_radius=8, wrap="word",
        )
        self.log.pack(fill=ctk.BOTH, expand=True, padx=24, pady=(0, 16))

    def _apply_colors(self):
        mode = "light" if not self.dark_mode else "dark"
        self.palette = dict(self.palettes[mode])
        c = self.palette

        self.root.configure(fg_color=c["bg"])
        self.main_frame.configure(fg_color=c["bg"])

        self.header_frame.configure(fg_color=c["card"], border_color=c["card_border"])
        self.title_label.configure(text_color=c["text"])
        self.subtitle_label.configure(text_color=c["muted"])
        self.version_label.configure(text_color=c["accent"])

        self.settings_card.configure(fg_color=c["card"], border_color=c["card_border"])
        self.config_label.configure(text_color=c["text"])
        self.theme_label.configure(text="\U0001f319" if self.dark_mode else "\u2600\ufe0f")

        for child in self.grid_frame.winfo_children():
            if isinstance(child, ctk.CTkLabel) and child not in list(self.inset_labels.values()) + [self.dpi_label]:
                child.configure(text_color=c["label"])

        for key in self.edge_inset_vars:
            lbl = self.inset_labels[key]
            lbl.configure(text_color=c["text"])

        self.dpi_label.configure(text_color=c["text"])

        self.flatten_check.configure(
            fg_color=c["input_bg"],
            text_color=c["text"],
            border_color=c["input_border"],
            hover_color=c["input_border"],
        )

        self.files_card.configure(fg_color=c["card"], border_color=c["card_border"])
        self.files_title.configure(text_color=c["text"])

        for w in self.files_card.winfo_children():
            if isinstance(w, ctk.CTkLabel) and w not in [self.files_title]:
                w.configure(text_color=c["label"])

        self.input_entry.configure(
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["input_border"],
        )
        self.output_entry.configure(
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["input_border"],
        )
        self.browse_input_btn.configure(
            fg_color=c["primary"], hover_color=c["primary_hover"],
            text_color="#FFFFFF",
        )
        self.browse_output_btn.configure(
            fg_color=c["primary"], hover_color=c["primary_hover"],
            text_color="#FFFFFF",
        )

        self.btn_run.configure(
            fg_color=c["primary"],
            hover_color=c["primary_hover"],
            text_color="#FFFFFF",
        )

        self.progress.configure(
            fg_color=c.get("card_border", "#E2E8F0"),
            progress_color=c["success"],
        )
        self.progress_label.configure(text_color=c["log"])

        self.log_frame.configure(fg_color=c["card"], border_color=c["card_border"])

    def _toggle_theme(self):
        self.dark_mode = self.theme_switch.get() == 1
        ctk.set_appearance_mode("dark" if self.dark_mode else "light")
        self._apply_colors()

    def _refresh_slider_labels(self):
        dpi = self._snap_dpi(int(self.bleed_dpi_var.get()))
        self.bleed_dpi_var.set(dpi)
        for side_key, inset_var in self.edge_inset_vars.items():
            inset_px = int(round(inset_var.get()))
            self.inset_labels[side_key].configure(text=f"{inset_px} px")
        self.dpi_label.configure(text=f"{dpi} DPI")

    def _update_inset_label(self, side_key):
        inset_px = int(round(self.edge_inset_vars[side_key].get()))
        self.inset_labels[side_key].configure(text=f"{inset_px} px")

    def _snap_dpi(self, dpi):
        dpi = round(dpi / 30) * 30
        return max(300, min(600, dpi))

    def _update_dpi_label(self, val):
        dpi = self._snap_dpi(int(float(val)))
        self.bleed_dpi_var.set(dpi)
        self.dpi_label.configure(text=f"{dpi} DPI")

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
        self.log.insert(ctk.END, f"{text}\n")
        self.log.see(ctk.END)

    def write_log_threadsafe(self, text):
        self.root.after(0, lambda: self.write_log(text))

    def update_progress(self, value, label=""):
        self.progress.set(value / 100.0)
        if label:
            self.progress_label.configure(text=label)

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
        self.btn_run.configure(
            state=ctk.DISABLED,
            text="PROCESSING... DO NOT CLOSE WINDOW",
        )
        self.progress.set(0)
        self.progress_label.configure(text="Initializing...")

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
            i, o,
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
        self.progress.set(1.0)

        if success:
            self.progress_label.configure(text="Completed - 100%")
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
            self.progress_label.configure(text="Failed")
            self.write_log("")
            self.write_log("PROCESS FAILED - Check log for details")
            self.show_error(
                "Error",
                "An error occurred during processing.\nCheck the log for more details.",
            )

        self.btn_run.configure(
            state=ctk.NORMAL,
            text="GENERATE PDF WITH BLEED",
        )


if __name__ == "__main__":
    root = ctk.CTk()
    app = App(root)
    root.mainloop()
