"""Isolated pipeline stages and seam (de)serialization.

This package exposes the translation pipeline as a set of stage functions
operating on a single explicit :class:`~nwn_translator.pipeline.stages.PipelineState`,
plus an artifact layer (:mod:`nwn_translator.pipeline.artifacts`) that persists
the data passed between stages so any stage can run from a saved input.
"""
