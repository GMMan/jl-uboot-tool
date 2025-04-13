"""Binary isd_config.ini utilities"""

__all__ = [
    'create_binary_from_ini',
    'create_full_binary_ini',
]

import configparser
import struct
from .crc import jl_crc16

def _is_integer(s: str):
    if len(s) == 0:
        return False
    
    if s[0] in ('+', '-'):
        s = s[1:]
    return s.isdigit()

def create_binary_from_ini(path, section_name='SYS_CFG_PARAM'):
    """Create a binary INI from the given INI file and section name"""
    config = configparser.ConfigParser()
    config.read(path)
    
    binary = bytearray()
    if section_name in config:
        section = config[section_name]
        for (k, v) in section:
            if len(v) == 0 or len(v) > 32:
                continue

            is_int = _is_integer(v)
            if is_int:
                val_len = 4
            else:
                val_len = len(v)

            binary.append(val_len)
            binary.extend(k.upper().encode('ascii'))
            binary.append(0)  # null terminator

            if is_int:
                binary.extend(struct.pack('<i', v))
            else:
                binary.extend(v.encode())
        
    binary.append(0)  # value length of 0 or > 32 will stop parsing
    return bytes(binary)

def create_full_binary_ini(chipkey_blob=b'\x00' * 32, ini_blob=b'\x00'):
    """Create a full isd_config.ini blob from chipkey blob and binary INI"""
    if len(chipkey_blob) != 32:
        raise ValueError('Chipkey blob is not 32 bytes in length')
    
    data = bytearray(chipkey_blob)
    chipkey_crc = jl_crc16(data)
    data.extend(struct.pack('<H', chipkey_crc))
    data.extend(ini_blob)
    return bytes(data)
