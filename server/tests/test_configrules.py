# SPDX-License-Identifier: AGPL-3.0-or-later
"""The option-rule language: reading, evaluating, checking against the options, and plain error messages."""
import pytest

from kosha.configrules import ALWAYS, RuleError, check, normal, parse

OPTS = {"MOTOR": {"5K5", "7K5", "11K"}, "VOLTAGE": {"230", "415"}, "SEAL": {"PTFE", "VITON"}}


def ok(rule, **choice):
    return parse(rule).eval(choice)


def test_simple_and_combined_rules():
    assert parse("") is ALWAYS and parse("   ") is ALWAYS and parse(None) is ALWAYS
    assert ok("MOTOR = 7K5", MOTOR="7K5") and not ok("MOTOR = 7K5", MOTOR="11K")
    assert ok("motor = 7k5", MOTOR="7K5")                                      # case does not matter
    assert ok("MOTOR != 7K5", MOTOR="11K") and not ok("MOTOR != 7K5", MOTOR="7K5")
    assert ok("MOTOR in (5K5, 7K5)", MOTOR="5K5") and not ok("MOTOR in (5K5,7K5)", MOTOR="11K")
    assert ok("MOTOR not in (5K5, 7K5)", MOTOR="11K")
    assert ok("MOTOR = 11K and VOLTAGE = 415", MOTOR="11K", VOLTAGE="415")
    assert not ok("MOTOR = 11K and VOLTAGE = 415", MOTOR="11K", VOLTAGE="230")
    assert ok("VOLTAGE = 230 or MOTOR = 11K and SEAL = PTFE", VOLTAGE="230", MOTOR="5K5", SEAL="VITON")
    assert not ok("(VOLTAGE = 230 or MOTOR = 11K) and SEAL = PTFE", VOLTAGE="230", MOTOR="5K5", SEAL="VITON")
    assert ok("not SEAL = PTFE", SEAL="VITON") and not ok("not (SEAL = PTFE or SEAL = VITON)", SEAL="VITON")


@pytest.mark.parametrize("rule, words", [
    ("MOTOR", "write = (or !=)"), ("MOTOR =", "ends too early"), ("MOTOR = 7K5 VOLTAGE = 415", "Join conditions"),
    ("MOTOR in 7K5", "Expected ("), ("(MOTOR = 7K5", ") is missing"), ("MOTOR == 7K5", "Expected a choice"),
    ("MOTOR = 7K5; drop", "can't read"), ("and = 1", "Expected an option code"), ("MOTOR not = 1", "not, write in"),
])
def test_errors_say_what_to_write(rule, words):
    with pytest.raises(RuleError, match=words.replace("(", r"\(").replace(")", r"\)")):
        parse(rule)


def test_rules_are_checked_against_the_options():
    check("MOTOR in (5K5, 7K5) and SEAL = PTFE", OPTS)
    with pytest.raises(RuleError, match="There is no option FRAME. The options are MOTOR, SEAL, VOLTAGE."):
        check("FRAME = HEAVY", OPTS)
    with pytest.raises(RuleError, match="MOTOR has no choice 15K. Its choices are 11K, 5K5, 7K5."):
        check("MOTOR in (7K5, 15K)", OPTS)
    with pytest.raises(RuleError, match="A rule can be at most 500"):
        parse("MOTOR = 7K5 or " * 60 + "MOTOR = 5K5")


def test_rules_are_written_the_same_way_every_time():
    assert normal("motor in ( 5k5 ,7k5 )  and  seal=ptfe") == "MOTOR in (5K5, 7K5) and SEAL = PTFE"
    assert normal("") == ""
    assert parse(normal("not (seal = ptfe)")).eval({"SEAL": "VITON"})
