"""Small native Tk widgets shared by the RemoteVibecode agent pages."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk

from agent.dashboard import COLORS, FONT, TYPE


def apply_theme(root: tk.Misc) -> None:
    root.configure(bg=COLORS["bg"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "RV.Vertical.TScrollbar", background=COLORS["panel_raised"],
        troughcolor=COLORS["bg"], bordercolor=COLORS["bg"],
        arrowcolor=COLORS["muted"], relief="flat",
    )
    style.map("RV.Vertical.TScrollbar", background=[("active", COLORS["blue"])])


def icon_photo(root: tk.Misc, name: str, *, size=22, color=None) -> ImageTk.PhotoImage:
    """Render the small outline icon set used by the desktop navigation."""
    ink = color or COLORS["muted"]
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 24
    def xy(*values):
        return tuple(round(value * scale) for value in values)
    width = max(1, round(1.9 * scale))
    if name == "home":
        draw.line((xy(3, 11), xy(12, 3), xy(21, 11)), fill=ink, width=width, joint="curve")
        draw.rounded_rectangle(xy(5, 10, 19, 21), radius=round(1.5*scale), outline=ink, width=width)
        draw.rectangle(xy(10, 15, 14, 21), outline=ink, width=width)
    elif name == "phone":
        draw.rounded_rectangle(xy(6, 2, 18, 22), radius=round(2.5*scale), outline=ink, width=width)
        draw.line((xy(10, 19), xy(14, 19)), fill=ink, width=width)
    elif name == "link":
        draw.arc(xy(2, 8, 16, 22), 310, 135, fill=ink, width=width)
        draw.arc(xy(8, 2, 22, 16), 130, 315, fill=ink, width=width)
        draw.line((xy(9, 15), xy(15, 9)), fill=ink, width=width)
    elif name == "settings":
        draw.ellipse(xy(5, 5, 19, 19), outline=ink, width=width)
        draw.ellipse(xy(10, 10, 14, 14), outline=ink, width=width)
        for x1, y1, x2, y2 in ((12, 1, 12, 5), (12, 19, 12, 23),
                               (1, 12, 5, 12), (19, 12, 23, 12),
                               (4, 4, 7, 7), (17, 17, 20, 20),
                               (17, 7, 20, 4), (4, 20, 7, 17)):
            draw.line((xy(x1, y1), xy(x2, y2)), fill=ink, width=width)
    else:
        draw.arc(xy(3, 3, 21, 21), 38, 320, fill=ink, width=width)
        draw.line((xy(18, 3), xy(21, 3), xy(21, 7)), fill=ink, width=width, joint="curve")
    return ImageTk.PhotoImage(image, master=root)


class Surface(tk.Frame):
    """A responsive rounded card with a native widget content layer."""

    def __init__(self, parent: tk.Misc, **kwargs):
        super().__init__(parent, bg=COLORS["bg"], bd=0, highlightthickness=0, **kwargs)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], bd=0, highlightthickness=0, height=1)
        self.canvas.pack(fill="x", expand=True)
        self.body = tk.Frame(self.canvas, bg=COLORS["panel"], bd=0, highlightthickness=0)
        self.window = self.canvas.create_window((8, 8), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._body_changed, add="+")
        self.canvas.bind("<Configure>", self._canvas_changed, add="+")
        self.after_idle(self._sync)

    def _body_changed(self, _event=None):
        self._sync()

    def _canvas_changed(self, event):
        self.canvas.itemconfigure(self.window, width=max(1, event.width - 16))
        self._draw_shape(event.width, max(event.height, self.body.winfo_reqheight() + 16))

    def _sync(self):
        height = max(1, self.body.winfo_reqheight() + 16)
        self.canvas.configure(height=height)
        self.canvas.itemconfigure(self.window, width=max(1, self.canvas.winfo_width() - 16))
        self._draw_shape(self.canvas.winfo_width(), height)

    def _draw_shape(self, width, height):
        if width < 2 or height < 2:
            return
        scale = 3
        radius = min(17, width // 2, height // 2) * scale
        image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        ImageDraw.Draw(image).rounded_rectangle(
            (scale, scale, (width - 1) * scale, (height - 1) * scale),
            radius=radius, fill=COLORS["panel"], outline=COLORS["edge"], width=scale)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        self._shape_image = ImageTk.PhotoImage(image, master=self.canvas)
        self.canvas.delete("surface")
        self.canvas.create_image(0, 0, image=self._shape_image, anchor="nw", tags="surface")
        self.canvas.tag_lower("surface")


class AppButton(tk.Button):
    """Keyboard-native button with visible hover, pressed and focus feedback."""

    def __init__(self, parent: tk.Misc, text: str, command, *, primary=False,
                 compact=False, **kwargs):
        self.primary = primary
        self.base_bg = COLORS["blue"] if primary else COLORS["panel_raised"]
        self.hover_bg = COLORS["blue_hover"] if primary else COLORS["panel_hover"]
        self.press_bg = COLORS["cyan_dark"] if primary else COLORS["edge"]
        self.gradient_images = None
        size = (196, 38) if compact else (252, 46)
        if primary:
            self.gradient_images = tuple(ImageTk.PhotoImage(self._gradient(size, variant))
                                         for variant in (0, 1, 2))
        super().__init__(
            parent, text=text, command=command, takefocus=True,
            bg=self.base_bg, fg=COLORS["white"], activebackground=self.press_bg,
            activeforeground=COLORS["white"], disabledforeground=COLORS["dim"],
            relief="flat", bd=0, cursor="hand2", font=(FONT, TYPE["button"]),
            highlightthickness=1, highlightbackground=COLORS["edge"],
            highlightcolor=COLORS["focus"],
            image=self.gradient_images[0] if self.gradient_images else "",
            compound="center" if self.gradient_images else "none",
            width=size[0] if self.gradient_images else None,
            height=size[1] if self.gradient_images else None,
            padx=0 if self.gradient_images else (14 if compact else 18),
            pady=0 if self.gradient_images else (7 if compact else 10),
            **kwargs,
        )
        self.bind("<Enter>", self._hover, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<FocusIn>", self._focus, add="+")
        self.bind("<FocusOut>", self._leave, add="+")
        self.bind("<ButtonPress-1>", self._pressed, add="+")
        self.bind("<ButtonRelease-1>", self._released, add="+")
        self.bind("<Return>", self._activate_from_keyboard, add="+")
        self.bind("<KP_Enter>", self._activate_from_keyboard, add="+")

    def _hover(self, _event=None):
        if str(self["state"]) != "disabled":
            self.configure(bg=self.hover_bg)
            if self.gradient_images:
                self.configure(image=self.gradient_images[1])

    def _leave(self, _event=None):
        self.configure(bg=self.base_bg)
        if self.gradient_images:
            self.configure(image=self.gradient_images[0])
        if self.focus_get() is not self:
            self.configure(highlightthickness=1)

    def _focus(self, _event=None):
        self.configure(highlightthickness=2)

    def _pressed(self, _event=None):
        if str(self["state"]) != "disabled":
            self.configure(bg=self.press_bg)
            if self.gradient_images:
                self.configure(image=self.gradient_images[2])

    def _released(self, _event=None):
        if str(self["state"]) != "disabled":
            self.configure(bg=self.hover_bg)
            if self.gradient_images:
                self.configure(image=self.gradient_images[1])

    def _activate_from_keyboard(self, _event=None):
        if str(self["state"]) != "disabled":
            self.invoke()
        return "break"

    @staticmethod
    def _gradient(size, variant):
        width, height = size
        stops = (("#4b32ff", "#536dff", "#00cce5"),
                 ("#6048ff", "#6680ff", "#32e1ef"),
                 ("#3723df", "#4058df", "#00a9c2"))[variant]
        rgb = [tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) for color in stops]
        image = Image.new("RGB", size)
        pixels = image.load()
        for x in range(width):
            amount = x / max(1, width - 1) * 2
            index = min(1, int(amount))
            local = amount - index
            color = tuple(int(rgb[index][c] * (1 - local) + rgb[index + 1][c] * local)
                          for c in range(3))
            for y in range(height):
                pixels[x, y] = color
        return image


class ScrollableFrame(tk.Frame):
    """A vertical scroll area whose children retain their native font sizes."""

    def __init__(self, parent: tk.Misc, *, background=None, **kwargs):
        bg = background or COLORS["bg"]
        super().__init__(parent, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                       style="RV.Vertical.TScrollbar")
        self.content = tk.Frame(self.canvas, bg=bg, bd=0, highlightthickness=0)
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self._set_scrollbar)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._content_configure, add="+")
        self.canvas.bind("<Configure>", self._canvas_configure, add="+")
        self._scroll_bindtag = "RVScrollable"
        self.bind_class(self._scroll_bindtag, "<MouseWheel>", self._wheel)
        self.bind_class(self._scroll_bindtag, "<Button-4>", self._wheel)
        self.bind_class(self._scroll_bindtag, "<Button-5>", self._wheel)
        for widget in (self, self.canvas, self.content):
            widget.bind("<MouseWheel>", self._mousewheel, add="+")
            widget.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"), add="+")
            widget.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"), add="+")

    def _set_scrollbar(self, first, last):
        self.scrollbar.set(first, last)
        overflow = float(first) > .001 or float(last) < .999
        if overflow and not self.scrollbar.winfo_manager():
            self.scrollbar.pack(side="right", fill="y")
        elif not overflow and self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()

    def _content_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_configure(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _mousewheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def bind_descendants(self) -> None:
        """Route child wheel events here and reveal controls as keyboard focus moves."""
        def visit(widget):
            if widget is not self.content and isinstance(widget, (tk.Text, tk.Scrollbar, ttk.Scrollbar)):
                return
            tags = list(widget.bindtags())
            if self._scroll_bindtag not in tags:
                tags.insert(1, self._scroll_bindtag)
                widget.bindtags(tuple(tags))
            try:
                can_focus = str(widget.cget("takefocus")) not in ("", "0")
            except tk.TclError:
                can_focus = False
            if can_focus:
                widget.bind("<FocusIn>", lambda event: self.canvas.after_idle(
                    lambda target=event.widget: self.ensure_visible(target)), add="+")
            for child in widget.winfo_children():
                visit(child)
        visit(self.content)

    def _wheel(self, event):
        first, last = self.canvas.yview()
        if first <= 0 and last >= 1:
            return None
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            units = int(-delta / 120)
            if units == 0:
                units = -1 if delta > 0 else 1
        self.canvas.yview_scroll(units, "units")
        return "break"

    def ensure_visible(self, widget):
        try:
            if not widget.winfo_exists():
                return
            self.canvas.update_idletasks()
            region = self.canvas.bbox("all")
            if not region:
                return
            top = widget.winfo_rooty() - self.canvas.winfo_rooty() + self.canvas.canvasy(0)
            bottom = top + max(widget.winfo_height(), 1)
            view_top = self.canvas.canvasy(0)
            view_bottom = view_top + self.canvas.winfo_height()
            total = max(1, region[3] - region[1])
            if top < view_top:
                self.canvas.yview_moveto(max(0, top / total))
            elif bottom > view_bottom:
                self.canvas.yview_moveto(min(1, max(0, (bottom - self.canvas.winfo_height()) / total)))
        except tk.TclError:
            return


def heading(parent: tk.Misc, text: str, *, size=None, color=None, **kwargs) -> tk.Label:
    return tk.Label(parent, text=text, bg=COLORS["panel"], fg=color or COLORS["white"],
                    font=(FONT, size or TYPE["section"], "bold"), anchor="w", **kwargs)


def body_label(parent: tk.Misc, text: str, *, muted=False, size=None,
               background=None, **kwargs) -> tk.Label:
    return tk.Label(parent, text=text, bg=background or COLORS["panel"],
                    fg=COLORS["muted"] if muted else COLORS["white"],
                    font=(FONT, size or TYPE["body"]), anchor="w", justify="left",
                    **kwargs)


def card(parent: tk.Misc, *, padding=18, **kwargs) -> tuple[Surface, tk.Frame]:
    surface = Surface(parent, **kwargs)
    inner = tk.Frame(surface.body, bg=COLORS["panel"], padx=padding, pady=padding)
    inner.pack(fill="both", expand=True)
    return surface, inner
