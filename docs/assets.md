# Presentation assets

[Home](../README.md)

The MemHarmBench emblem combines an AI agent, a memory chip, and two connecting rings. Three nodes represent the parties whose interests may be affected; a coral instruction fragment marks the injection risk. Navy, teal, purple, and gold connect the identity to the paper's figures. Light and dark versions are included.

The original transparent artwork was created with the built-in image generation tool. Its source PNGs and exact prompts are saved in [`assets/brand/source/`](../assets/brand/source/generation.json). The SVG files are self-contained containers embedding those PNGs, **not editable vector traces**. The animation adds a gentle float and sequential node pulses to the saved artwork. It loops every 3.6 seconds and starts with a complete emblem.

## Paper figures

The English and Chinese README overviews display Figures 1–3 from the current manuscript:

1. A user–agent dialogue illustrating a compromised recipient in a complete summary.
2. Measured conditional target-adoption rates across four agent–backbone configurations.
3. A conceptual distinction between authority, retention, and adoption.

Each figure has a PNG preview and an original PDF with selectable text. Figure 2 pools eligible IPI triggers over three repetitions per configuration; it is not a success rate over all scheduled episodes. Figures 1 and 3 are illustrations. See [asset provenance and checksums](../assets/README.md) for source filenames, eligibility conditions, and file identities. The full manuscript and per-episode execution evidence are not included.

`paper-taxonomy.png` remains in the taxonomy section without a current figure number. The older `paper-overview.png` and `framework*` assets remain for compatibility but are not displayed in the overview.

## Rebuild

Rebuild the static logos, icons, and light/dark animations from the saved originals:

```bash
python -m pip install pillow resvg-py
python scripts/render_brand.py
```

To also rebuild the optional framework diagrams and record the terminal demonstration, install this package and run:

```bash
python scripts/render_assets.py
```

`--vectors-only` retains its existing meaning: rebuild static presentation assets and logo animations while skipping the CLI recording. `--animations-only` rebuilds the animated logos and CLI demonstration. These commands do not regenerate the paper figures or call an image or language model service.

The terminal recording executes the real offline `catalog --limit 6`, `demo`, and `validate` commands. It animates command entry and reveals captured output; the full text is in `assets/demo-transcript.txt`. It does not simulate model execution. System fonts are used without redistributing font files.

| Asset | Use |
| --- | --- |
| `assets/logo-animated.gif`, `logo-animated-dark.gif` | Transparent, 72-frame README animations |
| `assets/logo.png`, `logo-dark.png` | Transparent static marks |
| `assets/logo.svg`, `logo-dark.svg` | Self-contained SVG wrappers around the generated PNGs |
| `assets/brand/source/` | Original artwork and generation prompts |
| `assets/icon.png`, `icon.ico` | Small application icons |
| `assets/paper-figure-{1,2,3}-{en,zh}.{png,pdf}` | The first three manuscript figures in both languages |
| `assets/paper-taxonomy.png` | Victim-centered taxonomy illustration |
| `assets/demo.gif`, `demo.png`, `demo-transcript.txt` | Offline CLI demonstration and complete captured output |

The assets and rendering scripts are distributed under the repository's [MIT License](../LICENSE).
