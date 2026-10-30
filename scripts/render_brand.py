"""Package the AI-generated MemHarmBench emblem and animate its memory signals.

The two alpha PNG originals in assets/brand/source are the source of truth.
No model calls are made here. The emblem remains raster artwork; moving particles
are vector geometry. Animated SVGs and offline GIFs share one motion definition.
Install the optional render dependencies with: pip install pillow resvg-py
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOURCE = ASSETS / "brand" / "source"
SIZE = 320
FRAMES = 144
DURATION = 50
SECONDS = FRAMES * DURATION / 1000


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


def _orbit(rx: float, ry: float, angle: float) -> tuple[str, float]:
    """Use real path lengths so browser animation and offline frames agree."""
    a = math.radians(angle)
    points = []
    for i in range(241):
        t = math.tau * i / 240
        x, y = rx * math.cos(t), ry * math.sin(t)
        points.append((160 + x * math.cos(a) - y * math.sin(a),
                       160 + x * math.sin(a) + y * math.cos(a)))
    length = sum(math.dist(p, q) for p, q in zip(points, points[1:]))
    path = 'M' + 'L'.join(f'{x:.3f} {y:.3f}' for x, y in points) + 'Z'
    return path, length


def _neon_backdrop(t: float) -> tuple[str, str]:
    """Dark aurora panel, independent of the surrounding page's color scheme."""
    defs = '''<clipPath id="panel"><rect x="4" y="4" width="312" height="312" rx="28"/></clipPath>
      <linearGradient id="night" x2="1" y2="1">
        <stop stop-color="#091633"/><stop offset=".48" stop-color="#18103D"/>
        <stop offset="1" stop-color="#062C3C"/>
      </linearGradient>
      <linearGradient id="rim" x2="1" y2="1">
        <stop stop-color="#49EAF1"/><stop offset=".45" stop-color="#7469F5"/>
        <stop offset=".75" stop-color="#F456D5"/><stop offset="1" stop-color="#46CFE2"/>
      </linearGradient>'''
    for name, color in [('cyan', '#00DAF5'), ('pink', '#EF35D5'), ('blue', '#5654FF')]:
        defs += (f'<radialGradient id="nebula-{name}"><stop stop-color="{color}" stop-opacity=".74"/>'
                 f'<stop offset=".42" stop-color="{color}" stop-opacity=".28"/>'
                 f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
    dx, dy = 7 * math.sin(math.tau*t), 6 * math.cos(math.tau*t)
    opacity = .84 + .12 * math.sin(math.tau*t)
    aurora = (f'<g class="aurora" transform="translate({dx:.4f} {dy:.4f})" opacity="{opacity:.4f}">'
              '<ellipse cx="56" cy="96" rx="148" ry="178" fill="url(#nebula-cyan)"/>'
              '<ellipse cx="260" cy="224" rx="160" ry="176" fill="url(#nebula-pink)"/>'
              '<ellipse cx="172" cy="22" rx="174" ry="143" fill="url(#nebula-blue)"/>'
              '<ellipse cx="123" cy="305" rx="136" ry="95" fill="url(#nebula-cyan)"/></g>')
    rng = random.Random(843)
    stars = []
    for i in range(66):
        x, y = rng.uniform(14,306), rng.uniform(14,306)
        color = ['#7CEBF8', '#BDA6FF', '#F7A5E6'][i % 3]
        stars.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{rng.uniform(.35,.85):.2f}" '
                     f'fill="{color}" opacity="{rng.uniform(.22,.65):.2f}"/>')
    grid = ''.join(f'M16 {y}H304' for y in [248,264,280,296])
    grid += ''.join(f'M160 166L{x} 316' for x in range(-80,401,48))
    body = ('<rect x="4" y="4" width="312" height="312" rx="28" fill="url(#night)"/>'
            '<g clip-path="url(#panel)">' + aurora + ''.join(stars)
            + f'<path d="{grid}" fill="none" stroke="#70B6FA" stroke-width=".5" opacity=".10"/>'
            '<path d="M20 71V45Q20 20 45 20H71M249 300H275Q300 300 300 275V249" '
            'fill="none" stroke="url(#rim)" stroke-width="1.2" opacity=".46"/></g>'
            '<rect x="4.6" y="4.6" width="310.8" height="310.8" rx="27.4" '
            'fill="none" stroke="url(#rim)" stroke-width="1.2" opacity=".58"/>')
    return defs, body


def animated_svg(dark: bool = False, phase: float | None = None) -> str:
    """CSS-only SVG, or a deterministic frozen frame of the same 7.2-second loop.

    The saved 512px emblem is embedded unchanged; particles are vector geometry.
    No script, external fonts, network requests, or SVG filters are required.
    """
    live = phase is None
    t = 0.0 if live else phase % 1
    # Keep the legacy dark argument for callers; both page themes use the neon panel.
    colors = ['#20E8DB', '#60B9FF', '#BD92FF', '#FFD073', '#FF7CCF']
    background_defs, backdrop = _neon_backdrop(t)
    orbit_specs = [(145, 126, -24, 1), (134, 115, 34, -1)]
    defs, rings = [], []
    for i, (rx, ry, angle, direction) in enumerate(orbit_specs):
        path, length = _orbit(rx, ry, angle)
        defs.append(f'<path id="route{i}" d="{path}"/>')
        rings.append(f'<use href="#route{i}" fill="none" stroke="{colors[i]}" '
                     'stroke-width=".7" opacity=".27"/>')
        for j in range(5):
            color = colors[(j + i * 2) % len(colors)]
            start = length * (j / 5 + i * .09)
            # A broad glow, a bright head and three tapering trail segments.
            for behind, dash, width, opacity in [
                (30, 15, 1.3, .16), (18, 12, 1.8, .35), (7, 7, 2.5, .64),
                (0, .01, 17, .10), (0, .01, 9, .23), (0, .01, 4.7, 1),
                (0, .01, 1.6, .94),
            ]:
                offset = -start + direction * behind
                current = offset - direction * length * t
                color_here = '#FFFFFF' if width == 1.6 else color
                motion = (f'class="travel" style="--start:{offset:.4f};'
                          f'--end:{offset - direction * length:.4f}"' if live else '')
                rings.append(f'<use href="#route{i}" {motion} fill="none" '
                             f'stroke="{color_here}" stroke-width="{width}" '
                             f'stroke-linecap="round" stroke-dasharray="{dash} {length-dash:.4f}" '
                             f'stroke-dashoffset="{current:.4f}" opacity="{opacity}"/>')
    for name, color in [('teal', colors[0]), ('violet', colors[2]), ('gold', colors[3])]:
        defs.append(f'<radialGradient id="{name}"><stop stop-color="{color}" '
                    'stop-opacity=".22"/>'
                    f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
    glow = ('<ellipse cx="112" cy="153" rx="108" ry="112" fill="url(#teal)"/>'
            '<ellipse cx="214" cy="174" rx="98" ry="105" fill="url(#violet)"/>'
            '<ellipse cx="164" cy="81" rx="62" ry="60" fill="url(#gold)"/>')
    rng = random.Random(288)
    dust = []
    for i in range(32):
        a = math.tau * i / 32 + rng.uniform(-.05, .05)
        radius = rng.uniform(138, 150)
        x, y = 160 + radius * math.cos(a), 160 + radius * .94 * math.sin(a)
        dust.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{rng.uniform(.65,1.5):.2f}" '
                    f'fill="{colors[i % 5]}" opacity="{rng.uniform(.25,.65):.2f}"/>')
    dust = (f'<g class="dust" transform="rotate({-360*t:.3f} 160 160)">'
            + ''.join(dust) + '</g>')
    # Use an already-rendered transparent mark, keeping the AI-generated source untouched.
    emblem_bytes = (ASSETS / 'logo-dark.png').read_bytes()
    encoded = base64.b64encode(emblem_bytes).decode('ascii')
    dx, dy, tilt = 12 * math.cos(math.tau*t), 10 * math.sin(math.tau*t), 2 * math.sin(math.tau*t)
    core = (f'<g class="core" transform="translate({dx:.4f} {dy:.4f}) rotate({tilt:.4f} 160 160)">'
            f'<image x="38" y="38" width="244" height="244" href="data:image/png;base64,{encoded}"/>'
            '</g>')
    glints = []
    for i, (x, y) in enumerate([(48, 88), (267, 109), (79, 268), (243, 249)]):
        pulse = .38 + .26 * math.sin(math.tau * (2*t + i/4))
        glints.append(f'<path class="glint" style="animation-delay:-{i*SECONDS/8}s" '
                      f'd="M{x-3.5} {y}h7M{x} {y-3.5}v7" fill="none" '
                      f'stroke="{colors[i]}" stroke-width="1.1" stroke-linecap="round" opacity="{pulse:.3f}"/>')
    css = ''
    if live:
        keys, aurora_keys = [], []
        for i in range(33):
            a = math.tau * i / 32
            keys.append(f'{i/32*100:.4f}%{{transform:translate({12*math.cos(a):.4f}px,'
                        f'{10*math.sin(a):.4f}px) rotate({2*math.sin(a):.4f}deg)}}')
            aurora_keys.append(f'{i/32*100:.4f}%{{transform:translate({7*math.sin(a):.4f}px,'
                               f'{6*math.cos(a):.4f}px);opacity:{.84+.12*math.sin(a):.4f}}}')
        css = f'''<style>
          .core {{transform-origin:160px 160px;animation:drift {SECONDS}s linear infinite}}
          .travel {{animation:travel {SECONDS}s linear infinite}}
          .dust {{transform-origin:160px 160px;animation:turn {SECONDS}s linear infinite}}
          .glint {{animation:glint {SECONDS/2}s ease-in-out infinite}}
          .aurora {{animation:aurora {SECONDS}s linear infinite}}
          @keyframes drift {{{''.join(keys)}}}
          @keyframes aurora {{{''.join(aurora_keys)}}}
          @keyframes travel {{from{{stroke-dashoffset:var(--start)}}to{{stroke-dashoffset:var(--end)}}}}
          @keyframes turn {{from{{transform:rotate(0deg)}}to{{transform:rotate(-360deg)}}}}
          @keyframes glint {{0%,100%{{opacity:.2}}50%{{opacity:.7}}}}
          @media (prefers-reduced-motion:reduce) {{
            .core,.travel,.dust,.glint,.aurora {{animation:none}}
          }}
        </style>'''
    return _svg('<desc>A memory agent drifts up, down, left and right inside two '
                'counter-rotating particle streams over a deep indigo aurora with cyan and magenta light.</desc>'
                + '<defs>' + background_defs + ''.join(defs) + '</defs>' + css + backdrop + glow + ''.join(rings)
                + dust + core + ''.join(glints), 'MemHarmBench — persistent memory in motion')


def build_animations() -> None:
    """Export a dark neon animation, a still alternative, and legacy theme aliases."""
    import resvg_py
    from PIL import Image

    still = animated_svg(phase=0)
    (ASSETS / 'logo-neon.svg').write_text(still, encoding='utf-8', newline='\n')
    (ASSETS / 'logo-neon.png').write_bytes(resvg_py.svg_to_bytes(svg_string=still, width=640, height=640))
    (ASSETS / 'logo-animated.svg').write_text(animated_svg(), encoding='utf-8', newline='\n')
    frames, masks = [], []
    for index in range(FRAMES):
        source = animated_svg(phase=index / FRAMES)
        png = resvg_py.svg_to_bytes(svg_string=source, width=SIZE*2, height=SIZE*2)
        rgba = Image.open(io.BytesIO(png)).convert('RGBA').resize((SIZE,SIZE), Image.Resampling.LANCZOS)
        composite = Image.new('RGBA', rgba.size, '#101A38')
        composite.alpha_composite(rgba)
        frames.append(composite.convert('RGB'))
        masks.append(rgba.getchannel('A').point(lambda value: 255 if value < 2 else 0))
    samples = Image.new('RGB', (SIZE*4,SIZE))
    for i in range(4):
        samples.paste(frames[i*FRAMES//4], (i*SIZE,0))
    palette = samples.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    quantized = []
    for frame, mask in zip(frames,masks):
        output = frame.quantize(palette=palette, dither=Image.Dither.NONE)
        output.paste(255, mask=mask)
        quantized.append(output)
    quantized[0].save(ASSETS / 'logo-animated.gif', save_all=True, append_images=quantized[1:],
                      duration=DURATION, loop=0, disposal=2, transparency=255, optimize=False)
    for suffix in ('svg', 'gif'):
        (ASSETS / f'logo-animated-dark.{suffix}').write_bytes((ASSETS / f'logo-animated.{suffix}').read_bytes())


def audit() -> dict:
    """Check final alpha, loop metadata, frame variation, and safe viewports."""
    from PIL import Image, ImageChops, ImageStat

    result = {"source": "Built-in image_gen emblem; source PNGs retained without modification",
              "background": "Code-rendered dark neon aurora on both page themes", "themes": {}}
    animation_qa = {}
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
        changed = ImageChops.difference(first.convert('RGB'), gif.convert('RGB')).getbbox() is not None
        import xml.etree.ElementTree as ET
        svg_path = ASSETS / ('logo-animated-dark.svg' if dark else 'logo-animated.svg')
        svg_source = svg_path.read_text(encoding='utf-8')
        ET.fromstring(svg_source)
        accessible = 'prefers-reduced-motion:reduce' in svg_source
        self_contained = '<script' not in svg_source and 'href="http' not in svg_source
        neon_panel = 'id="night"' in svg_source and 'class="aurora"' in svg_source
        valid = (image.size == (512, 512) and transparent and corners and gif.n_frames == FRAMES
                 and gif.info.get("loop") == 0 and changed and accessible and self_contained and neon_panel
                 and gif_path.stat().st_size < 10*1024*1024)
        result["themes"][stem] = {"png_size": list(image.size), "has_alpha": transparent,
                                  "transparent_corners": corners, "gif_frames": gif.n_frames,
                                  "gif_duration_ms": DURATION * FRAMES,
                                  "gif_loop": gif.info.get("loop"), "frames_differ": changed,
                                  "gif_bytes": gif_path.stat().st_size,
                                  "svg_bytes": svg_path.stat().st_size,
                                  "reduced_motion": accessible, "self_contained": self_contained,
                                  "dark_neon_panel": neon_panel,
                                  "passed": valid}
        if not valid:
            raise ValueError(f"Brand asset validation failed: {stem}")
        frames, margins, durations = [], [], []
        for index in range(gif.n_frames):
            gif.seek(index)
            rgba = gif.convert('RGBA')
            x0, y0, x1, y1 = rgba.getchannel('A').getbbox()
            margins.append(min(x0, y0, gif.width-x1, gif.height-y1))
            durations.append(gif.info['duration'])
            matte = Image.new('RGBA', gif.size, '#0D1117' if dark else '#FFFFFF')
            matte.alpha_composite(rgba)
            frames.append(matte.convert('RGB'))
        differences = [sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3
                       for a,b in zip(frames,frames[1:]+frames[:1])]
        seam_ratio = differences[-1] / (sum(differences[:-1]) / (len(differences)-1))
        animation_qa[gif_path.name] = {
            'frames':gif.n_frames, 'duration_ms':sum(durations),
            'all_frames_clear_of_edge':min(margins)>0,
            'minimum_edge_margin_px':min(margins),
            'loop_seam_to_average_step':round(seam_ratio,4),
            'sha256':hashlib.sha256(gif_path.read_bytes()).hexdigest(),
        }
        if min(margins)<=0 or seam_ratio>2:
            raise ValueError(f'Clipped or discontinuous animation: {stem}')
    still_source = (ASSETS / 'logo-neon.svg').read_text(encoding='utf-8')
    ET.fromstring(still_source)
    if '<style>' in still_source or 'id="night"' not in still_source:
        raise ValueError('Reduced-motion alternative must preserve the neon panel without animation')
    for suffix in ('svg', 'gif'):
        if (ASSETS / f'logo-animated.{suffix}').read_bytes() != (ASSETS / f'logo-animated-dark.{suffix}').read_bytes():
            raise ValueError(f'Page themes have inconsistent neon artwork: {suffix}')
    (ASSETS / "brand" / "qa.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    (ASSETS / 'brand' / 'animation-qa.json').write_text(
        json.dumps(animation_qa,indent=2)+'\n',encoding='utf-8',newline='\n')
    return result


if __name__ == "__main__":
    build_static()
    build_animations()
    print(json.dumps(audit(), indent=2))
