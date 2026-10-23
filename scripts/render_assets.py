"""Rebuild the project identity, optional diagrams, and offline CLI walkthrough.

Optional presentation dependencies: pip install pillow resvg-py
Run from a checkout with the package installed: python scripts/render_assets.py
The walkthrough executes the real offline CLI. It never calls a model API.
"""
from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
NAVY = "#143565"
TEAL = "#15B8A6"
GOLD = "#F2B544"


def svg_document(body: str, width: int, height: int, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">'
            f'<title id="title">{html.escape(title)}</title>{body}</svg>\n')


def framework(dark: bool = False) -> str:
    bg = "#0E1929" if dark else "#F6F9FC"
    panel = "#15263D" if dark else "#FFFFFF"
    ink = "#E6EFFC" if dark else NAVY
    muted = "#A6BCD4" if dark else "#526A85"
    border = "#2B425C" if dark else "#DCE6F0"
    soft_teal = "#163D40" if dark else "#E8F8F5"
    soft_gold = "#493C25" if dark else "#FFF5DF"
    p: list[str] = [f'<rect width="1240" height="590" rx="24" fill="{bg}"/>',
                   '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9" fill="none" stroke="#7F96AE" stroke-width="1.6"/></marker></defs>']

    def text(x: int, y: int, value: str, size: int = 18, weight: int = 400, color: str | None = None):
        p.append(f'<text x="{x}" y="{y}" fill="{color or ink}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}">{html.escape(value)}</text>')

    def box(x: int, y: int, w: int, h: int, fill: str = panel):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{border}" stroke-width="1.5"/>')

    def arrow(x: int, y: int, x2: int, y2: int):
        p.append(f'<path d="M{x} {y}L{x2} {y2}" fill="none" stroke="#7F96AE" stroke-width="2.5" marker-end="url(#arrow)"/>')

    text(34, 43, "MEMHARMBENCH  /  EVALUATION FRAMEWORK", 14, 700, muted)
    text(34, 86, "A rule can outlive the session that introduced it.", 30, 700)
    text(34, 116, "Follow the input, retained rule, and later decision through a real agent's native memory.", 18, color=muted)

    box(34, 166, 226, 94, soft_gold)
    text(52, 195, "DPI · user message", 20, 700)
    text(52, 222, "Authorized-user channel", 16, color=muted)
    text(52, 244, "Unverified persistent rule", 16, color=muted)
    box(34, 280, 226, 94, soft_teal)
    text(52, 310, "IPI · external artifact", 20, 700)
    text(52, 337, "Untrusted content", 16, color=muted)
    text(52, 359, "Embedded instruction", 16, color=muted)

    box(310, 192, 245, 165)
    text(332, 223, "01", 16, 700, TEAL)
    text(332, 259, "Exposure session", 23, 700)
    text(332, 289, "The agent encounters", 18, color=muted)
    text(332, 316, "the adversarial rule.", 18, color=muted)
    box(615, 192, 245, 165)
    text(637, 223, "02", 16, 700, TEAL)
    text(637, 259, "Native memory", 23, 700)
    text(637, 289, "Capture what the agent", 18, color=muted)
    text(637, 316, "actually retains.", 18, color=muted)
    box(920, 192, 286, 165)
    text(942, 223, "03", 16, 700, TEAL)
    text(942, 259, "Fresh-session task", 23, 700)
    text(942, 289, "Observe the later decision", 18, color=muted)
    text(942, 316, "and its execution trace.", 18, color=muted)

    p.append('<path d="M260 213H282V275H304M260 327H282V275" fill="none" stroke="#7F96AE" stroke-width="2.5"/>')
    arrow(286, 275, 304, 275)
    arrow(560, 275, 608, 275)
    arrow(866, 275, 913, 275)
    text(480, 400, "Original attack carrier removed before the trigger", 17, 600, muted)

    box(34, 435, 521, 107)
    text(54, 466, "WHOSE INTERESTS ARE AT STAKE?", 13, 700, muted)
    text(54, 495, "User  ·  Workspace / Project Owner", 19, 600)
    text(54, 521, "External Stakeholder", 19, 600)
    box(615, 435, 245, 107, soft_teal)
    text(635, 464, "MEMORY EVIDENCE", 13, 700, muted)
    text(635, 496, "PMIR", 24, 700)
    text(635, 522, "Complete-rule retention", 16, color=muted)
    box(920, 435, 286, 107, soft_teal)
    text(940, 464, "DECISION + TRACE EVIDENCE", 13, 700, muted)
    text(940, 496, "C-ASR · UTDR · ATAR", 23, 700)
    text(940, 522, "Adoption · deviation · anomalies", 15, color=muted)
    text(34, 572, "Main evaluation uses PMIR-gated triggers. Ablations retain their own predeclared trigger schedules.", 14, color=muted)
    return svg_document("".join(p), 1240, 590, "MemHarmBench exposure, native memory, and fresh-session evaluation")


def framework_mobile(dark: bool = False) -> str:
    bg, panel = ("#0E1929", "#15263D") if dark else ("#F6F9FC", "#FFFFFF")
    ink, muted = ("#E6EFFC", "#A6BCD4") if dark else (NAVY, "#526A85")
    border = "#2B425C" if dark else "#DCE6F0"
    soft_teal, soft_gold = ("#163D40", "#493C25") if dark else ("#E8F8F5", "#FFF5DF")
    p = [f'<rect width="720" height="1010" rx="24" fill="{bg}"/>']

    def text(x, y, value, size=24, weight=400, color=None):
        p.append(f'<text x="{x}" y="{y}" fill="{color or ink}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}">{html.escape(value)}</text>')

    def box(x, y, w, h, color=panel):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{color}" stroke="{border}" stroke-width="1.5"/>')

    def arrow(y):
        p.append(f'<path d="M360 {y}v26m-9-9l9 9 9-9" fill="none" stroke="#7F96AE" stroke-width="3"/>')

    text(28, 44, "MEMHARMBENCH / EVALUATION", 19, 700, muted)
    text(28, 90, "A rule can outlive its session.", 35, 700)
    text(28, 127, "Trace memory into the next decision.", 25, color=muted)
    box(28, 163, 324, 130, soft_gold)
    text(48, 199, "DPI · user message", 25, 700)
    text(48, 235, "Authorized-user channel", 21, color=muted)
    text(48, 266, "Unverified rule", 21, color=muted)
    box(368, 163, 324, 130, soft_teal)
    text(388, 199, "IPI · external artifact", 25, 700)
    text(388, 235, "Untrusted content", 21, color=muted)
    text(388, 266, "Embedded instruction", 21, color=muted)
    arrow(304)
    box(28, 342, 664, 120)
    text(49, 380, "01  Exposure session", 30, 700)
    text(49, 424, "The agent encounters the adversarial rule.", 25, color=muted)
    arrow(472)
    box(28, 510, 664, 136)
    text(49, 550, "02  Native memory", 30, 700)
    text(49, 592, "Capture the complete retained rule.", 25, color=muted)
    text(49, 626, "PMIR · memory evidence", 22, 600, TEAL)
    arrow(654)
    text(127, 710, "Original attack carrier removed", 23, 600, muted)
    box(28, 736, 664, 136)
    text(49, 776, "03  Fresh-session task", 30, 700)
    text(49, 818, "Observe the later decision and tool trace.", 25, color=muted)
    text(49, 852, "C-ASR · UTDR · ATAR", 23, 600, TEAL)
    text(28, 920, "WHOSE INTERESTS ARE AT STAKE?", 19, 700, muted)
    text(28, 954, "User · Workspace / Project Owner", 25, 600)
    text(28, 986, "External Stakeholder", 25, 600)
    return svg_document("".join(p), 720, 1010, "Mobile overview of exposure, memory, and new-session evaluation")


def font_file(mono: bool = False, bold: bool = False) -> str | None:
    """Use a system font without distributing third-party font files."""
    base = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = ([base / "CascadiaMono.ttf", base / "consola.ttf",
                   Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")]
                  if mono else [base / ("arialbd.ttf" if bold else "arial.ttf"),
                                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")])
    return next((str(p) for p in candidates if p.exists()), None)


def build_vectors():
    import resvg_py
    from render_brand import build_static
    ASSETS.mkdir(parents=True, exist_ok=True)
    build_static()
    for name, builder in (("framework", framework), ("framework-mobile", framework_mobile)):
        for dark in (False, True):
            stem = f"{name}{'-dark' if dark else ''}"
            source = builder(dark)
            (ASSETS / f"{stem}.svg").write_text(source, encoding="utf-8")
            (ASSETS / f"{stem}.png").write_bytes(resvg_py.svg_to_bytes(svg_string=source, zoom=2))


def capture(command: list[str]) -> str:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", NO_COLOR="1")
    result = subprocess.run([sys.executable, "-m", "memharmbench_v84", *command], cwd=ROOT,
                            env=env, check=True, capture_output=True, text=True, encoding="utf-8")
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout.strip())
    if str(ROOT) in output or str(ROOT).replace("\\", "/") in output:
        raise ValueError("Offline CLI output contains a local checkout path; do not publish it.")
    return output


def build_animated_logos() -> None:
    """Reuse the saved generated identity; no generation service is called."""
    from render_brand import build_animations
    build_animations()


def build_demo():
    from PIL import Image, ImageDraw, ImageFont
    commands = [["catalog", "--limit", "6"], ["demo"], ["validate"]]
    captures = [("memharmbench " + " ".join(cmd), capture(cmd)) for cmd in commands]
    transcript = "\n\n".join(f"$ {command}\n{output}" for command, output in captures) + "\n"
    (ASSETS / "demo-transcript.txt").write_text(transcript, encoding="utf-8", newline="\n")
    mono_path, sans_path = font_file(True), font_file(False)
    mono = ImageFont.truetype(mono_path, 17) if mono_path else ImageFont.load_default(size=17)
    title = ImageFont.truetype(sans_path, 19) if sans_path else ImageFont.load_default(size=19)
    small = ImageFont.truetype(sans_path, 15) if sans_path else ImageFont.load_default(size=15)
    frames = []
    durations = []
    for n, (command, output) in enumerate(captures):
        lines = []
        for line in output.splitlines():
            lines.extend(textwrap.wrap(line, width=113, replace_whitespace=False, drop_whitespace=False) or [""])
        # Excerpts are explicit; the companion transcript retains every output line.
        if len(lines) > 21:
            lines = lines[:20] + ["... [excerpt; complete output in assets/demo-transcript.txt]"]
        frame = Image.new("RGB", (1240, 690), "#0D1728")
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((14, 14, 1226, 676), 18, fill="#111F33", outline="#30435B", width=1)
        draw.rounded_rectangle((15, 15, 1225, 70), 18, fill="#1C2D44")
        draw.rectangle((15, 44, 1225, 70), fill="#1C2D44")
        for i, col in enumerate(("#E98685", "#E9BF6B", "#63BFA6")):
            draw.ellipse((37+i*24, 34, 48+i*24, 45), fill=col)
        draw.text((136, 29), "MemHarmBench  /  offline dataset walkthrough", font=title, fill="#E6EFFB")
        draw.text((42, 95), "$", font=mono, fill=TEAL)
        draw.text((65, 95), command, font=mono, fill="#E5F0FF")
        for i, line in enumerate(lines):
            color = "#88DBCD" if re.match(r"^\s*(PASS|OK|VALID|Validated|MemHarmBench|\[)", line) else "#C2D0E3"
            draw.text((42, 136+i*23), line, font=mono, fill=color)
        draw.line((40, 628, 1200, 628), fill="#30435B")
        draw.text((42, 645), "REAL CLI OUTPUT  ·  NO MODEL CALLS  ·  INPUT DATA ONLY", font=small, fill="#9BB2CD")
        for i in range(3):
            draw.rounded_rectangle((1104+i*29, 650, 1123+i*29, 656), 3, fill=TEAL if i == n else "#405671")
        if n == 1:
            frame.save(ASSETS / "demo.png")
        # Reveal only text actually emitted by the CLI, then leave time to read.
        def append_frame(visible: int, typed: int, duration: int) -> None:
            animated = frame.copy()
            overlay = ImageDraw.Draw(animated)
            overlay.rectangle((64, 91, 1200, 123), fill="#111F33")
            overlay.text((65, 95), command[:typed], font=mono, fill="#E5F0FF")
            if typed < len(command):
                cursor = 65 + overlay.textlength(command[:typed], font=mono)
                overlay.rectangle((cursor+2, 97, cursor+10, 114), fill=TEAL)
            if visible < len(lines):
                overlay.rectangle((40, 133+visible*23, 1200, 625), fill="#111F33")
            progress = (n + visible / max(1, len(lines))) / 3
            overlay.line((40, 616, 1200, 616), fill="#30435B", width=3)
            overlay.line((40, 616, 40+1160*progress, 616), fill=TEAL, width=3)
            frames.append(animated)
            durations.append(duration)

        for typed in range(0, len(command), 3):
            append_frame(0, typed, 60)
        for visible in range(0, len(lines)+1, 2):
            append_frame(visible, len(command), 90)
        append_frame(len(lines), len(command), [3000, 5000, 3500][n])
    frames[0].save(ASSETS / "demo.gif", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--vectors-only", action="store_true", help="Skip the installed-CLI recording.")
    modes.add_argument("--animations-only", action="store_true", help="Rebuild animated logos and the offline CLI GIF only.")
    args = parser.parse_args()
    if not args.animations_only:
        build_vectors()
    build_animated_logos()
    if not args.vectors_only:
        build_demo()
    print("Presentation assets generated in assets/ (no model calls).")


if __name__ == "__main__":
    main()
