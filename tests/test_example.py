"""
Test suite for the example command module.

Pure functions are tested directly with plain asserts. The Typer wrappers
are tested separately through the CLI runner, only to confirm the wiring
(argument parsing, exit codes, output), not the underlying logic.
"""

from typer.testing import CliRunner

from gencli.commands.example import reverse_words, slugify, word_count
from gencli.main import app

runner = CliRunner()


class TestSlugify:
    def test_lowercases_and_hyphenates(self):
        assert slugify("Hello, World!") == "hello-world"

    def test_strips_punctuation(self):
        assert slugify("A/B: testing (v2)") == "a-b-testing-v2"

    def test_empty_string(self):
        assert slugify("") == ""


class TestWordCount:
    def test_counts_case_insensitively(self):
        assert word_count("The the THE cat") == {"the": 3, "cat": 1}

    def test_ignores_punctuation(self):
        assert word_count("to be, or not to be!") == {
            "to": 2,
            "be": 2,
            "or": 1,
            "not": 1,
        }

    def test_empty_string(self):
        assert word_count("") == {}


class TestReverseWords:
    def test_reverses_word_order(self):
        assert reverse_words("the quick brown fox") == "fox brown quick the"

    def test_single_word(self):
        assert reverse_words("hello") == "hello"

    def test_empty_string(self):
        assert reverse_words("") == ""


def test_example_help():
    result = runner.invoke(app, ["example", "--help"])
    assert result.exit_code == 0
    assert "reference text utilities" in result.output


def test_slug_command():
    result = runner.invoke(app, ["example", "slug", "Hello, World!"])
    assert result.exit_code == 0
    assert "hello-world" in result.output


def test_word_count_command():
    result = runner.invoke(app, ["example", "word-count", "a a b"])
    assert result.exit_code == 0
    assert "a: 2" in result.output
    assert "b: 1" in result.output


def test_reverse_command():
    result = runner.invoke(app, ["example", "reverse", "one two three"])
    assert result.exit_code == 0
    assert "three two one" in result.output
