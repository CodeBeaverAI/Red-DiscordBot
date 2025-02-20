import argparse
import asyncio
import discord
import logging
import pytest
import sys
from discord import (
    __version__,
)
from enum import (
    IntEnum,
)
from redbot.core._cli import (
    ExitCodes,
    confirm,
    interactive_config,
    message_cache_size_int,
    non_negative_int,
    parse_cli_flags,
)
from redbot.core.utils._internal_utils import (
    cli_level_to_log_level,
)
from typing import (
    Optional,
)


def test_non_negative_and_message_cache_int():
    """
    Test non_negative_int and message_cache_size_int functions.

    This test verifies that:
    - non_negative_int properly converts valid string input to integer.
    - non_negative_int raises an error if a negative number is provided.
    - message_cache_size_int returns the proper integer if the provided string is 1000 or above.
    - message_cache_size_int raises an error if the provided number is less than 1000.
    """
    assert non_negative_int("10") == 10
    with pytest.raises(argparse.ArgumentTypeError) as exc_neg:
        non_negative_int("-5")
    assert "non-negative" in str(exc_neg.value).lower()
    assert message_cache_size_int("1500") == 1500
    with pytest.raises(argparse.ArgumentTypeError) as exc_cache:
        message_cache_size_int("500")
    assert "greater than or equal to 1000" in str(exc_cache.value)


def test_confirm(monkeypatch):
    """
    Test the confirm function for various scenarios:
    - Returning True when user enters "yes".
    - Returning False when user enters "no".
    - Using the default value when input is empty.
    - Re-prompting when input is invalid.
    - Handling KeyboardInterrupt and EOFError by exiting with appropriate codes.
    """
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert confirm("Test?") is True
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert confirm("Test?") is False
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert confirm("Test?", default=True) is True
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert confirm("Test?", default=False) is False
    responses = iter(["maybe", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    assert confirm("Test?") is True

    def raise_keyboard(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_keyboard)
    with pytest.raises(SystemExit) as excinfo:
        confirm("Test?")
    assert excinfo.value.code == ExitCodes.SHUTDOWN

    def raise_eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(SystemExit) as excinfo:
        confirm("Test?")
    assert excinfo.value.code == ExitCodes.INVALID_CLI_USAGE


def test_parse_cli_flags_parses_args():
    """
    Test parse_cli_flags function for correct parsing of CLI flags, including:
    - Setting the positional instance name.
    - Accumulation of verbosity flags and correct logging level conversion.
    - Sorting of provided prefixes in reverse order.
    """
    test_args = ["testinstance", "--verbose", "-v", "--prefix", "!", "--prefix", "?"]
    args = parse_cli_flags(test_args)
    assert args.instance_name == "testinstance"
    expected_log_level = cli_level_to_log_level(2)
    assert args.logging_level == expected_log_level
    expected_prefixes = sorted(["!", "?"], reverse=True)
    assert args.prefix == expected_prefixes


class DummyValue:

    def __init__(self):
        self.value = None

    async def set(self, value):
        self.value = value


class DummyConfig:

    def __init__(self):
        self.token = DummyValue()
        self.prefix = DummyValue()


class DummyRed:

    def __init__(self):
        self._config = DummyConfig()


@pytest.mark.asyncio
async def test_interactive_config_valid(monkeypatch):
    """
    Test interactive_config by simulating user inputs for both token and prefix.

    The test creates a dummy red instance with a fake _config that records the token
    and prefix values. It then simulates inputs for a valid token (a 50-character string),
    a valid prefix, and confirmation that accepts the chosen prefix. The test ensures that:
      - The returned token is the valid token.
      - The dummy red instance has its config values set appropriately.
    """
    red = DummyRed()
    inputs = iter(["a" * 50, "!", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    returned_token = await interactive_config(
        red, token_set=False, prefix_set=False, print_header=False
    )
    assert returned_token == "a" * 50
    assert red._config.token.value == "a" * 50
    assert red._config.prefix.value == ["!"]


@pytest.mark.asyncio
async def test_interactive_config_token_set(monkeypatch, capsys):
    """
    Test interactive_config when token is already set.
    This simulation only needs to configure the prefix because token_set is True.
    The test simulates an overly long prefix that gets rejected and then a valid one,
    and also asserts that the configuration header is printed.
    """
    red = DummyRed()
    inputs = iter(["abcdefghijk", "no", "!", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    token_returned = await interactive_config(
        red, token_set=True, prefix_set=False, print_header=True
    )
    captured = capsys.readouterr()
    assert "Red - Discord Bot | Configuration process" in captured.out
    assert token_returned is None
    assert red._config.prefix.value == ["!"]


@pytest.mark.asyncio
async def test_interactive_config_both_token_and_prefix_set(monkeypatch, capsys):
    """
    Test interactive_config when both token_set and prefix_set are True.
    In this scenario, no user input should be requested, and the function should
    simply print the header (if print_header is True) and return None without
    modifying the configuration values.
    """

    class DummyValue:

        def __init__(self):
            self.value = None

        async def set(self, value):
            self.value = value

    class DummyConfig:

        def __init__(self):
            self.token = DummyValue()
            self.prefix = DummyValue()

    class DummyRed:

        def __init__(self):
            self._config = DummyConfig()

    red = DummyRed()
    ret_token = await interactive_config(
        red, token_set=True, prefix_set=True, print_header=True
    )
    captured = capsys.readouterr()
    assert "Red - Discord Bot | Configuration process" in captured.out
    assert ret_token is None
    assert red._config.token.value is None
    assert red._config.prefix.value is None


@pytest.mark.asyncio
async def test_interactive_config_invalid_then_valid(monkeypatch):
    """
    Test interactive_config with an initially invalid token (too short) followed by a valid token,
    and a scenario where an overly long prefix is rejected before a valid prefix is provided.
    This test simulates user input through monkeypatch to trigger the re-prompting logic.
    """

    class DummyValue:

        def __init__(self):
            self.value = None

        async def set(self, value):
            self.value = value

    class DummyConfig:

        def __init__(self):
            self.token = DummyValue()
            self.prefix = DummyValue()

    class DummyRed:

        def __init__(self):
            self._config = DummyConfig()

    red = DummyRed()
    responses = iter(["short", "a" * 50, "verylongprefix", "no", "!", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    ret_token = await interactive_config(
        red, token_set=False, prefix_set=False, print_header=False
    )
    assert ret_token == "a" * 50
    assert red._config.token.value == "a" * 50
    assert red._config.prefix.value == ["!"]


def test_parse_cli_flags_rpc_options():
    """
    Test parse_cli_flags to ensure that RPC related flags are correctly parsed.
    This test verifies that:
    - The --rpc flag is correctly interpreted as True.
    - The --rpc-port flag sets the RPC port to the specified value.
    - Other unrelated values (like the instance name) are properly set.
    """
    test_args = ["myinstance", "--rpc", "--rpc-port", "7000"]
    args = parse_cli_flags(test_args)
    assert args.instance_name == "myinstance"
    assert args.rpc is True
    assert args.rpc_port == 7000
