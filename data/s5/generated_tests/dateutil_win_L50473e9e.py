import datetime
import struct
from unittest.mock import patch
from dateutil.tz.win import tzwinlocal

TZLOCALKEYNAME = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
TZKEYNAME = r"SYSTEM\CurrentControlSet\Control\TimeZone"

def mock_valuestodict(key):
    return {
        "StandardName": "Coordinated Universal Time",
        "DaylightName": "Coordinated Universal Time",
        "Bias": 0,
        "StandardBias": 0,
        "DaylightBias": 60,
        "Display": "UTC",
        "StandardStart": struct.pack("=8h", 0, 1, 0, 0, 0, 0, 0, 0),
        "DaylightStart": struct.pack("=8h", 0, 1, 0, 0, 0, 0, 0, 0),
    }

def test_tzwinlocal_dstoffset_mutation():
    with patch("dateutil.tz.win.winreg.ConnectRegistry") as mock_connect, \
         patch("dateutil.tz.win.winreg.OpenKey") as mock_open, \
         patch("dateutil.tz.win.valuestodict", side_effect=mock_valuestodict):
        
        mock_handle = mock_connect.return_value.__enter__.return_value
        mock_tzlocalkey = mock_open.return_value.__enter__.return_value
        mock_tzkey = mock_open.return_value.__enter__.return_value
        
        tz = tzwinlocal()
        
        expected_dst_offset = datetime.timedelta(minutes=-60)
        assert tz._dst_offset == expected_dst_offset, \
            f"Expected _dst_offset to be {expected_dst_offset}, got {tz._dst_offset}"
