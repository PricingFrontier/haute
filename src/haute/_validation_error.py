"""The marker type for haute-authored validation messages.

Lives in its own module (re-exported by :mod:`haute.errors`) because it
deliberately derives from ``ValueError`` rather than ``HauteError`` — the
``errors`` module keeps its promise that every exception class *defined* there
roots at ``HauteError``, while this class follows the stdlib-base pattern that
module's docstring describes.
"""

from __future__ import annotations


class HauteValidationError(ValueError):
    """Marker for haute-authored validation messages on the ``ValueError`` channel.

    The training-path failure mapper surfaces validation wording verbatim as a
    job's terminal message. That promotion is keyed on this type — provenance
    by enforcement — so a dependency's plain ``ValueError`` can never ride the
    curated channel; it takes the type-only fallback wording instead. Raise
    this (or a subclass such as ``TrainingConfigError``) at haute's own
    validation sites whose message is written for the user.

    Deliberately derives from ``ValueError`` rather than ``HauteError`` so
    every existing ``except ValueError`` validation handler keeps catching it.

    Do not raise this inside a pydantic validator: pydantic wraps any
    ``ValueError`` subclass into its own ``ValidationError``, which drops the
    marker — the message would then take the fallback, not travel verbatim.
    """


class ConfigSettingError(HauteValidationError):
    """A node setting a config parser refuses: a banding factor, a rating table.

    Raised where a parser reads a node's configuration, in the editor and at
    run time alike; ``str(error)`` is the message. ``setting`` is the config
    key the refused value sits under (``factors``, ``tables``,
    ``combinedOutputs``), ``fix`` one correction when the parser knows it, and
    ``values`` the configured values the message quotes: a node's saved
    configuration, unlike the column and output names a message also names,
    so a caller that must not disclose configuration knows what it would.

    A ``ValueError`` like every other validation message, so a node that
    raises it fails on its own in a preview rather than failing the run.
    """

    def __init__(
        self,
        message: str,
        *,
        setting: str,
        fix: str | None = None,
        values: tuple[object, ...] = (),
    ) -> None:
        self.setting = setting
        self.fix = fix
        self.values = values
        super().__init__(message)
