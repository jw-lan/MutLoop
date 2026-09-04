import pytest
from unittest.mock import patch
from dateutil.tz.win import tzwinlocal
from dateutil.tz.win import TZKEYNAME
import winreg

def test_tzwinlocal_mutation_kill():
    """Test that tzwinlocal correctly reads registry key with proper path."""
    # Mock the registry functions to control the behavior
    with patch('dateutil.tz.win.winreg.ConnectRegistry') as mock_connect, \
         patch('dateutil.tz.win.winreg.OpenKey') as mock_open, \
         patch('dateutil.tz.win.valuestodict') as mock_valuestodict:
        
        # Setup mock registry handles
        mock_handle = mock_connect.return_value.__enter__.return_value
        mock_tzlocalkey = mock_open.return_value.__enter__.return_value
        
        # Mock valuestodict for the local key
        mock_valuestodict.side_effect = [
            {
                "StandardName": "Test Standard",
                "DaylightName": "Test Daylight",
                "Bias": 300,
                "StandardBias": 0,
                "DaylightBias": -60,
                "StandardStart": b'\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
                "DaylightStart": b'\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            },
            {
                "Display": "Test Display"
            }
        ]
        
        # Track the key name used in OpenKey
        opened_keys = []
        
        def mock_open_key(handle, keyname, *args, **kwargs):
            opened_keys.append(keyname)
            return mock_open.return_value
        
        mock_open.side_effect = mock_open_key
        
        # Create the tzwinlocal instance
        tz = tzwinlocal()
        
        # Verify the correct registry key was opened
        expected_key = f"{TZKEYNAME}\\Test Standard"
        assert expected_key in opened_keys, f"Expected key '{expected_key}' to be opened, but got {opened_keys}"
        
        # Verify the display value was set correctly
        assert tz._display == "Test Display"
