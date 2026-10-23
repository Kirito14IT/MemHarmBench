"""Package the AI-generated MemHarmBench emblem and animate its memory signal.

The two alpha PNG originals in assets/brand/source are the source of truth.
No model calls are made here. SVGs embed that bitmap; they are not vector traces.
Install the optional render dependencies with: pip install pillow resvg-py
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOURCE = ASSETS / "brand" / "source"
SIZE = 320
FRAMES = 72
DURATION = 50


def _source(dark: bool = False):
    from PIL import Image

    path = SOURCE / ("logo-dark.png" if dark else "logo-light.png")
    image = Image.open(path).convert("RGBA")
    # Threshold only determines the viewport. The original alpha is unchanged.
    bbox = image.getchannel("A").point(lambda value: 255 if value > 127 else 0).getbbox()
    if bbox is None:
        raise ValueError(f"Empty source emblem: {path.name}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return image.size, bbox, encoded


def _svg(body: str, title: str, size: int = SIZE) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 {SIZE} {SIZE}" role="img" aria-labelledby="title">'
            f'<title id="title">{html.escape(title)}</title>{body}</svg>\n')


def _emblem(dark: bool = False, lift: float = 0):
    dimensions, bbox, encoded = _source(dark)
    x0, y0, x1, y1 = bbox
    scale = 276 / max(x1 - x0, y1 - y0)
    tx = (SIZE - (x1 - x0) * scale) / 2 - x0 * scale
    ty = (SIZE - (y1 - y0) * scale) / 2 - y0 * scale + lift
    drawing = (f'<image x="{tx:.6f}" y="{ty:.6f}" '
               f'width="{dimensions[0] * scale:.6f}" height="{dimensions[1] * scale:.6f}" '
               f'href="data:image/png;base64,{encoded}"/>')
    # Node centers are measured from the generated source, in native pixels.
    nodes = [(627, 263), (293, 926), (997, 867)]
    node_positions = [(tx + x * scale, ty + y * scale) for x, y in nodes]
    return drawing, node_positions, scale


def build_static() -> None:
    """Export both transparent themes plus small application icons."""
    import resvg_py
    from PIL import Image

    ASSETS.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        stem = "logo-dark" if dark else "logo"
        emblem, _, _ = _emblem(dark)
        source = _svg(emblem, "MemHarmBench: agent memory, cross-session influence, and stakeholders")
        (ASSETS / f"{stem}.svg").write_text(source, encoding="utf-8", newline="\n")
        (ASSETS / f"{stem}.png").write_bytes(resvg_py.svg_to_bytes(svg_string=source, width=512, height=512))
    icon = Image.open(ASSETS / "logo.png").convert("RGBA")
    icon.resize((64, 64), Image.Resampling.LANCZOS).save(ASSETS / "icon.png")
    icon.save(ASSETS / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


def build_animations() -> None:
    """Float the emblem and pass a gentle pulse through its three stakeholder nodes."""
    import resvg_py
    from PIL import Image

    colors = ["#F2B544", "#15B8A6", "#9B78CF"]
    for dark in (False, True):
        matte = "#0D1117" if dark else "#FFFFFF"
        frames = []
        for index in range(FRAMES):
            phase = index / FRAMES
            lift = -3.5 * math.sin(math.tau * phase)
            emblem, nodes, scale = _emblem(dark, lift)
            halos = []
            for node_index, ((x, y), color) in enumerate(zip(nodes, colors)):
                local = (phase * 3 - node_index) % 3
                if local < 0.9:
                    radius = (118 + 30 * local / 0.9) * scale
                    opacity = 0.65 * (1 - local / 0.9)
                    halos.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}" '
                                 f'fill="none" stroke="{color}" stroke-width="2" opacity="{opacity:.4f}"/>')
            source = _svg("".join(halos) + emblem, "MemHarmBench memory signal")
            png = resvg_py.svg_to_bytes(svg_string=source, width=SIZE * 2, height=SIZE * 2)
            rgba = Image.open(io.BytesIO(png)).convert("RGBA").resize((SIZE, SIZE), Image.Resampling.LANCZOS)
            # GIF has binary alpha. Theme-specific mattes preserve antialiased edges.
            composite = Image.new("RGBA", rgba.size, matte)
            composite.alpha_composite(rgba)
            frame = composite.convert("RGB").quantize(colors=127, method=Image.Quantize.MEDIANCUT)
            mask = rgba.getchannel("A").point(lambda value: 255 if value < 128 else 0)
            frame.paste(127, mask=mask)
            frame.info["transparency"] = 127
            frames.append(frame)
        path = ASSETS / ("logo-animated-dark.gif" if dark else "logo-animated.gif")
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=DURATION,
                       loop=0, disposal=2, transparency=127, optimize=False)


def audit() -> dict:
    """Check final alpha, loop metadata, frame variation, and safe viewports."""
    from PIL import Image, ImageChops

    result = {"source": "Built-in image_gen; source PNGs retained without modification", "themes": {}}
    for dark in (False, True):
        stem = "logo-dark" if dark else "logo"
        image = Image.open(ASSETS / f"{stem}.png").convert("RGBA")
        alpha = image.getchannel("A")
        transparent = alpha.getextrema()[0] == 0
        corners = all(alpha.getpixel(point) == 0 for point in [(0, 0), (511, 0), (0, 511), (511, 511)])
        gif_path = ASSETS / ("logo-animated-dark.gif" if dark else "logo-animated.gif")
        gif = Image.open(gif_path)
        first = gif.convert("RGBA")
        gif.seek(FRAMES // 3)
        changed = ImageChops.difference(first, gif.convert("RGBA")).getbbox() is not None
        valid = (image.size == (512, 512) and transparent and corners and gif.n_frames == FRAMES
                 and gif.info.get("loop") == 0 and changed)
        result["themes"][stem] = {"png_size": list(image.size), "has_alpha": transparent,
                                  "transparent_corners": corners, "gif_frames": gif.n_frames,
                                  "gif_duration_ms": DURATION * FRAMES,
                                  "gif_loop": gif.info.get("loop"), "frames_differ": changed,
                                  "gif_bytes": gif_path.stat().st_size, "passed": valid}
        if not valid:
            raise ValueError(f"Brand asset validation failed: {stem}")
    (ASSETS / "brand" / "qa.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    build_static()
    build_animations()
    print(json.dumps(audit(), indent=2))
