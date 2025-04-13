""" CRC calculation routines """

__all__ = [
    'jl_crc8',
    'jl_crc16',
    'jl_crc32'
]

import crcmod
import crcmod.predefined

jl_crc8 = crcmod.predefined.mkPredefinedCrcFun('crc-8-maxim')
jl_crc16 = crcmod.predefined.mkPredefinedCrcFun('xmodem')
jl_crc32 = crcmod.mkCrcFun(0x104C11DB7, initCrc=0x26536734, rev=True)
