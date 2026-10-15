"""git's drivers for the test-quote file: ``python -m haute._test_quote_driver dump FILE``
shows it as text in a diff, and ``... merge BASE OURS THEIRS`` merges it quote by quote.

An entry module of its own, which nothing imports: run as ``python -m haute.cli._test_quotes``,
the module the commands live in would be imported once by its package and run again as
``__main__``, and Python warns of that on every diff and merge. The commands run as the group
alone, without the CLI's process set-up, whose log line would join the text a diff shows.
"""

from haute.cli._test_quotes import test_quotes

if __name__ == "__main__":
    test_quotes()
