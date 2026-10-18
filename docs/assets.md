# Presentation assets

[Home](../README.md)

The MemHarmBench mark is an original vector drawing: a folded memory page, an **M**, and a connected node. Deep navy (`#143565`) anchors the identity, teal (`#15B8A6`) marks the cross-session connection, and gold (`#F2B544`) accents the page fold. It does not reuse another project's logo.

The framework diagram explains the protocol, not experimental findings. Both the logo and framework have light- and dark-background variants. SVGs remain editable; PNGs are exported for uses that require raster images. The logo PNG retains a transparent background. Small PNG and ICO icons are also included.

## Rebuild

From a checkout with the package installed:

```bash
python -m pip install pillow resvg-py
python scripts/render_assets.py
```

Use `--vectors-only` to regenerate the identity and framework without recording the CLI.

The recording script executes the actual offline `catalog --limit 6`, `demo`, and `validate` commands, captures their standard output, and renders that text into terminal frames. The 13-second GIF is a compact recording of those outputs; it does not simulate an agent run. Long output is explicitly excerpted in the image, with the complete capture preserved in `assets/demo-transcript.txt`. No model calls or research outcomes are involved.

The script uses a system font and does not distribute font files. Small rendering differences between operating systems are expected. Generated image files are static, self-contained assets with no scripts or remote image references.

| Asset | Use |
| --- | --- |
| `assets/logo.svg`, `logo-dark.svg` | Editable transparent project marks |
| `assets/logo.png`, `logo-dark.png` | Transparent raster marks |
| `assets/icon.png`, `icon.ico` | Small icons |
| `assets/framework.svg`, `framework-dark.svg` | Editable protocol overview |
| `assets/framework.png`, `framework-dark.png` | Raster protocol overview |
| `assets/framework-mobile*.svg`, `framework-mobile*.png` | Vertical overview for narrow screens |
| `assets/demo.gif` | Actual offline CLI output, animated |
| `assets/demo.png` | Static view of the offline example |
| `assets/demo-transcript.txt` | Full command output used for the animation |

Original artwork and its generation script are covered by the repository's MIT License. The assets are hand-authored SVG and code-rendered terminal graphics; no image-generation model or paid API is used.
