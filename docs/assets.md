# Presentation assets

[Home](../README.md)

The MemHarmBench mark is an original vector drawing: a folded memory page, an **M**, and a connected node. Deep navy (`#143565`) anchors the identity, teal (`#15B8A6`) marks the cross-session connection, and gold (`#F2B544`) accents the page fold. It does not reuse another project's logo.

The README uses the two original raster figures from the MemHarmBench manuscript:

- `assets/paper-overview.png`: Figure 1, the exposure–memory–trigger workflow and benchmark organization.
- `assets/paper-taxonomy.png`: Figure 2, victim categories and protected interests.

These are byte-for-byte copies of the PNGs referenced by the manuscript, without redrawing, cropping, or added overlays. They explain the design and taxonomy; they contain no measured experimental outcomes. The README links each image to its full-resolution version. The paper PDF and TeX sources are not distributed here.

The README displays looping GIF variants of the original logo, with a gentle vertical float, a pulsing connected node, and a small orbiting signal. Light and dark versions are selected with a `<picture>` element. The original SVG/PNG variants remain available. Its PNG retains transparency, and small PNG/ICO icons are included. Earlier simplified `framework*` drawings remain available as optional assets but are no longer displayed in the README.

## Rebuild

From a checkout with the package installed:

```bash
python -m pip install pillow resvg-py
python scripts/render_assets.py
```

Use `--vectors-only` to regenerate the static marks, earlier vector diagrams, and animated marks without recording the CLI. Use `--animations-only` to rebuild just the animated marks and CLI demo. The script does not regenerate or overwrite either manuscript figure.

The recording script executes the actual offline `catalog --limit 6`, `demo`, and `validate` commands, captures their standard output, and renders that text into terminal frames. The looping GIF animates command entry and progressively reveals the captured output; it does not simulate an agent run. Long output is explicitly excerpted in the image, with the complete capture preserved in `assets/demo-transcript.txt`. No model calls or research outcomes are involved.

The script uses a system font and does not distribute font files. Small rendering differences between operating systems are expected. Generated image files are self-contained assets with no scripts or remote image references.

| Asset | Use |
| --- | --- |
| `assets/logo-animated.gif`, `logo-animated-dark.gif` | Looping transparent marks for light and dark README themes |
| `assets/logo.svg`, `logo-dark.svg` | Editable transparent project marks |
| `assets/logo.png`, `logo-dark.png` | Transparent raster marks |
| `assets/icon.png`, `icon.ico` | Small icons |
| `assets/paper-overview.png` | Original manuscript Figure 1, shown in the README overview |
| `assets/paper-taxonomy.png` | Original manuscript Figure 2, shown with the attack taxonomy |
| `assets/framework*.svg`, `assets/framework*.png` | Earlier optional simplified diagrams; not used in the README |
| `assets/demo.gif` | Actual offline CLI output, animated |
| `assets/demo.png` | Static view of the offline example |
| `assets/demo-transcript.txt` | Full command output used for the animation |

Original project artwork and its generation script are covered by the repository's MIT License. The logo and earlier vector diagrams are hand-authored SVG; the animated marks rasterize the same SVG geometry into GIF frames; the terminal demo is rendered from actual CLI output. The manuscript figures are supplied raster artwork. Copying these figures and recording the offline demo require no model calls.
