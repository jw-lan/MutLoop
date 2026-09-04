import pytest
from dateutil.tz.win import tzwinbase

class ConcreteTZWinBase(tzwinbase):
    def __init__(self, std_offset, dst_offset, stddayofweek, dstdayofweek,
                 stdweeknumber, dstweeknumber, stdhour, dsthour,
                 stdminute, dstminute, std_abbr, dst_abbr):
        self._std_offset = std_offset
        self._dst_offset = dst_offset
        self._stddayofweek = stddayofweek
        self._dstdayofweek = dstdayofweek
        self._stdweeknumber = stdweeknumber
        self._dstweeknumber = dstweeknumber
        self._stdhour = stdhour
        self._dsthour = dsthour
        self._stdminute = stdminute
        self._dstminute = dstminute
        self._std_abbr = std_abbr
        self._dst_abbr = dst_abbr

def test_eq_mutation_and_to_or():
    # Two objects that differ only in _dst_abbr, but have same _std_abbr
    obj1 = ConcreteTZWinBase(
        std_offset=1, dst_offset=2, stddayofweek=0, dstdayofweek=0,
        stdweeknumber=1, dstweeknumber=1, stdhour=0, dsthour=0,
        stdminute=0, dstminute=0, std_abbr="EST", dst_abbr="EDT"
    )
    obj2 = ConcreteTZWinBase(
        std_offset=1, dst_offset=2, stddayofweek=0, dstdayofweek=0,
        stdweeknumber=1, dstweeknumber=1, stdhour=0, dsthour=0,
        stdminute=0, dstminute=0, std_abbr="EST", dst_abbr="CDT"
    )
    # Original: requires both _std_abbr and _dst_abbr equal -> False
    # Mutated:  requires _std_abbr equal OR _dst_abbr equal -> True
    assert (obj1 == obj2) is False
