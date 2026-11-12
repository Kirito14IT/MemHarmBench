"""Read-only loader for the frozen inputs downloaded from Hugging Face."""

from .compile import build_all, load_case_catalog

__all__ = ["build_all", "load_case_catalog"]
