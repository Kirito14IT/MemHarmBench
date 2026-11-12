"""Read-only compatibility entry point for checking the downloaded release.

The historical command name is retained for existing tooling. Dataset identities
are distributed only by the pinned Hugging Face release and are never rebuilt
or rewritten by this command. Download the inputs with
``python scripts/download_dataset.py`` before running it.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memharmbench_v84.public_validation import validate_public


def main() -> int:
    print(json.dumps(validate_public(ROOT), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
