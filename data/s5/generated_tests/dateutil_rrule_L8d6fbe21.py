import datetime
from dateutil.rrule import rrule

def test_kill_cor_and_to_or():
    # Create an until datetime with tzinfo
    until = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    
    # Call rrule without dtstart, with until having tzinfo
    # Original: if until and until.tzinfo: -> True (since until is truthy and has tzinfo)
    # Mutated: if until or until.tzinfo: -> True (same result)
    # This case doesn't kill, need a case where until is falsy but until.tzinfo is truthy
    # Actually until is a datetime, always truthy, so need until=None but until.tzinfo would error
    # Wait, the condition is `until or until.tzinfo`, if until is None, then until.tzinfo raises AttributeError
    # But original `until and until.tzinfo` would short-circuit to None (falsy) and go to else branch
    # So we need until=None to kill: original goes to else (no tzinfo), mutated raises AttributeError
    # But that would cause an exception in mutated, which is a failure (kills)
    # However, we need to test observable behavior. Let's test with until=None
    # But rrule requires until to be a datetime? Let's check: rrule(until=None) is valid
    # Actually the code checks `if not dtstart:` and then `if until and until.tzinfo:`
    # If until is None, original goes to else (no tzinfo), mutated tries until.tzinfo -> AttributeError
    # So we can test that rrule with until=None and no dtstart works (original) vs fails (mutated)
    # But we need to assert something. Let's create a rule and check it works.
    
    # Test case: until=None (falsy) - original works, mutated raises AttributeError
    rule = rrule(freq=2, count=1, until=None)  # freq=2 is DAILY? Actually freq constants: YEARLY=0, MONTHLY=1, WEEKLY=2, DAILY=3
    # Use DAILY=3
    rule = rrule(freq=3, count=1, until=None)
    # Get the first occurrence - should work in original
    first = rule[0]
    assert first is not None
    # This will fail on mutated because rrule construction raises AttributeError
