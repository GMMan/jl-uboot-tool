from scsiio import SCSIDev
from scsiio.common import SCSIException
from jltech.crc import jl_crc8, jl_crc16
from jltech.cipher import jl_crc_cipher, cipher_bytes
import os, time, random, struct
from serial import Serial

class JL_MSCDevice:
    """ Class for handling of the JieLi Mass Storage devices """

    def __init__(self, info):
        self.path = info['path']
        self.open()

    def __enter__(self):
        return self

    def __exit__(self, *args, **kvargs):
        self.close()

    #-------------------------------------------#

    def open(self):
        print("Waiting for [%s]" % self.path, end='', flush=True)

        # TODO: proper handling for non-UBOOT devices!

        while True:
            # try to open the device *right now*, if we fail for whatever reason,
            # we try again (should probably only ignore the 'file not found' and 'permission denied' stuff)
            try:
                self.dev = SCSIDev(self.path)
            except:
                print('.', end='', flush=True)
                time.sleep(.5)
                continue

            print(" try!", end='', flush=True)

            # try to get inquiry data
            try:
                vendor, product, prodrev = self.inquiry()
            except SCSIException:
                print(" fail!", end='', flush=True)
                self.dev.close()
                time.sleep(.5)
                continue

            # if it's an UBOOT device then we'll proceed
            if product.startswith('UBOOT'):
                break

            # otherwise try to enter this mode...
            # product.startswith(('UDISK','DEVICE'))
            else:
                ldr = JL_LoaderV2(self.dev)

                try:
                    ldr.online_device() # any command will suffice

                    # if it didn't fail, then assume we did get it.
                    #  (in case the fw handles the protocol!)
                    #  - Temporary fix! -
                    break
                except SCSIException:
                    pass

            self.dev.close()
            time.sleep(.5)

        print(" ok (%s %s %s)" % (vendor, product, prodrev))

    def close(self):
        self.dev.close()

    #-------------------------------------------#

    def inquiry(self):
        """ Send an Inquiry request and return manufacturer, product and revision strings """

        data = bytearray(36)
        self.dev.execute(b'\x12' + b'\x00\x00' + len(data).to_bytes(2, 'big')
                                               + b'\x00', None, data)

        return (
            str(data[8:16], 'ascii').strip(),
            str(data[16:32], 'ascii').strip(),
            str(data[32:36], 'ascii').strip()
        )

class SerialDevice:
    """Class for handling serial ports"""

    def __init__(self, info):
        self.path = info['path']
        self.is_download_tool = info['name'] and ('JLVirtualJtagSerial' in info['name']) # maybe Windows-only for now
        self.open()

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.close()

    #-------------------------------------------#

    def open(self):
        # baud is sane default that won't cause USB download tool to issue UART key
        self.dev = Serial(self.path, 115200, timeout=5.0)

    def close(self):
        self.dev.close()

    #-------------------------------------------#

    def send_uart_key(self):
        if self.is_download_tool:
            # Switch to 1-wire mode with power cycle enabled
            self.dev.baudrate = 3
            # Power cycle and send UART key, then set baudrate to 9600
            self.dev.baudrate = 9600
        else:
            # TODO: try to replicate UART key sequence using separate power signal
            # and clever data

            # Set baudrate to 9600
            self.dev.baudrate = 9600

###############################################################################################################

class JL_MSCProtocolBase:
    """
    Common stuff for the USB Mass Storage Class-based protocols
    """

    def __init__(self, dev):
        self.dev = dev

    def cmd_prepare_cdb(self, cmd, args):
        """ Prepare command's CDB """
        cdb = cmd.to_bytes(2, 'big') + args

        # pad the rest of CDB with 0xFF's
        if len(cdb) < 16:
            cdb += b'\xff' * (16 - len(cdb))

        return cdb

    def cmd_exec(self, cmd, args, check_response=True):
        """ Execute command - no data (response is received instead) """
        resp = bytearray(16)
        self.dev.execute(self.cmd_prepare_cdb(cmd, args), None, resp)

        rcmd = int.from_bytes(resp[:2], 'big')
        resp = bytes(resp[2:])

        if check_response:
            # Check if the command in response matched the sent one
            assert(cmd == rcmd)
            return resp
        else:
            # Return raw-ish response
            return (rcmd, resp)

    def cmd_exec_datain(self, cmd, args, dlen):
        """ Execute command - data in (receive from device) """
        data = bytearray(dlen)
        self.dev.execute(self.cmd_prepare_cdb(cmd, args), None, data)
        return bytes(data)

    def cmd_exec_dataout(self, cmd, args, data):
        """ Execute command - data out (send to device) """
        self.dev.execute(self.cmd_prepare_cdb(cmd, args), data, None)

    @property
    def supports_memory_commands(self):
        return True


class JL_LoaderV1(JL_MSCProtocolBase):
    """
    First gen loader protocol implementation, used in e.g. AC4100
    or even going back to the USB IDE for AC209N...
    """

    #
    # Commands
    #
    class Cmd:
        ERASE_FLASH_BLOCK           = 0xFB00
        WRITE_FLASH                 = 0xFB01
        ERASE_FLASH_CHIP            = 0xFB02
        ERASE_FLASH_SECTOR          = 0xFB03
        WRITE_MEMORY                = 0xFB04
        STH_FB05                    = 0xFB05
        STH_FB06                    = 0xFB06
        WRITE_WTW                   = 0xFB07
        READ_WTW                    = 0xFB08
        JUMP_TO_MEMORY              = 0xFB09

        FLASH_ID                    = 0xFC00
        STH_FC01                    = 0xFC01
        RESET                       = 0xFC02
        STH_FC03                    = 0xFC03
        STH_FC04                    = 0xFC04
        STH_FC05                    = 0xFC05
        STH_FC06                    = 0xFC06
        STH_FC07                    = 0xFC07
        STH_FC08                    = 0xFC08
        STH_FC09                    = 0xFC09
        GET_CHIPKEYISH              = 0xFC0A
        GET_ONLINE_DEVICE           = 0xFC0B
        SELECT_FLASH                = 0xFC0C

        READ_FLASH                  = 0xFD01

    #
    # Device types (as returned by the GET_ONLINE_DEVICE command)
    #
    class DevType:
        NONE                        = 0x00
        SPI_FLASH                   = 0x01
        SD_CARD                     = 0x02

    #-------------------------------------------#

    def flash_erase_block(self, addr):
        """ Erase flash block (64k) """
        self.cmd_exec(JL_LoaderV1.Cmd.ERASE_FLASH_BLOCK,
                            addr.to_bytes(4, 'big'))

    def flash_write(self, addr, data):
        """ Write flash """
        self.cmd_exec_dataout(JL_LoaderV1.Cmd.WRITE_FLASH,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big')
                    + b'\x00' + jl_crc16(data).to_bytes(2, 'little'), data)

    def flash_erase_chip(self):
        """ Erase flash chip """
        resp = self.cmd_exec(JL_LoaderV1.Cmd.ERASE_FLASH_CHIP, b'')

    def flash_erase_sector(self, addr):
        """ Erase flash sector (4k) """
        self.cmd_exec(JL_LoaderV1.Cmd.ERASE_FLASH_SECTOR,
                            addr.to_bytes(4, 'big'))

    def mem_write(self, addr, data):
        """ Write memory """
        self.cmd_exec_dataout(JL_LoaderV1.Cmd.WRITE_MEMORY,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big'), data)

    def mem_jump(self, addr, arg):
        """ Jump to memory """
        self.cmd_exec(JL_LoaderV1.Cmd.JUMP_TO_MEMORY,
                    addr.to_bytes(4, 'big') + arg.to_bytes(2, 'big'))

    def read_id(self):
        """ Read device ID """
        resp = self.cmd_exec(JL_LoaderV1.Cmd.FLASH_ID, b'')
        return int.from_bytes(resp[:3], 'big')

    def online_device(self, _):
        """ Get online device (unlike V2 protocol it only returns the device type) """
        resp = self.cmd_exec(JL_LoaderV1.Cmd.GET_ONLINE_DEVICE, b'')
        return resp[0]

    def flash_select(self, sel):
        """ Select SPI flash, sel is one of:
        0 = SPI_FLASH_CODE,
        1 = SPI_FLASH_DATA
        """
        self.cmd_exec(JL_LoaderV1.Cmd.SELECT_FLASH, bytes([sel]))

    def flash_read(self, addr, size):
        """ Read flash """
        return self.cmd_exec_datain(JL_LoaderV1.Cmd.READ_FLASH,
                addr.to_bytes(4, 'big') + size.to_bytes(2, 'big'), size)


class JL_UBOOT(JL_MSCProtocolBase):
    """
    UBOOT1.00 class implementation for all (or most?) UBOOT1.00 variants.
    """

    #
    # Commands
    #
    class Cmd:
        WRITE_MEMORY                = 0xFB06
        READ_MEMORY                 = 0xFD07
        JUMP_TO_MEMORY              = 0xFB08
        WRITE_MEMORY_RXGP           = 0xFB31

    #-------------------------------------------#

    def mem_write(self, addr, data):
        """ Write memory """
        self.cmd_exec_dataout(JL_UBOOT.Cmd.WRITE_MEMORY,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big')
                    + b'\x00' + jl_crc16(data).to_bytes(2, 'little'), data)

    def mem_read(self, addr, len):
        """ Read memory """
        return self.cmd_exec_datain(JL_UBOOT.Cmd.READ_MEMORY,
                addr.to_bytes(4, 'big') + len.to_bytes(2, 'big'), len)

    def mem_jump(self, addr, arg):
        """ Jump to memory (with argument) """
        self.cmd_exec(JL_UBOOT.Cmd.JUMP_TO_MEMORY,
                    addr.to_bytes(4, 'big') + arg.to_bytes(2, 'big'))

    def mem_write_rxgp(self, addr, data):
        """ Write memory (RxGp-encrypted payload, probably DVxx or DV15 specific) """
        self.cmd_exec_dataout(JL_UBOOT.Cmd.WRITE_MEMORY_RXGP,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big')
                    + b'\x00' + jl_crc16(data).to_bytes(2, 'little'), data)

class JL_LoaderV2(JL_MSCProtocolBase):
    """
    Loader class implementation for most loaders.
    """

    #
    # Commands
    #
    class Cmd:
        ERASE_FLASH_BLOCK           = 0xFB00
        ERASE_FLASH_SECTOR          = 0xFB01
        ERASE_FLASH_CHIP            = 0xFB02
        READ_STATUS                 = 0xFC03
        WRITE_FLASH                 = 0xFB04
        READ_FLASH                  = 0xFD05
        WRITE_MEMORY                = 0xFB06
        READ_MEMORY                 = 0xFD07
        JUMP_TO_MEMORY              = 0xFB08
        READ_KEY                    = 0xFC09
        GET_ONLINE_DEVICE           = 0xFC0A
        READ_ID                     = 0xFC0B
        RUN_APP                     = 0xFC0C
        SET_FLASH_CMD               = 0xFC0D
        FLASH_CRC16                 = 0xFC0E
        WRITE_KEY                   = 0xFC12
        FLASH_CRC16_RAW             = 0xFC13
        GET_USB_BUFF_SIZE           = 0xFC14
        GET_LOADER_VER              = 0xFC15
        GET_MASKROM_ID              = 0xFC16

    #
    # Device types (as returned by the GET_ONLINE_DEVICE command)
    #
    class DevType:
        NONE                        = 0x00
        SDRAM                       = 0x01
        SD_CARD                     = 0x02
        SPI0_NOR                    = 0x03
        SPI0_NAND                   = 0x04
        OTP                         = 0x05
        SD_CARD_2                   = 0x10
        SD_CARD_3                   = 0x11
        SD_CARD_4                   = 0x12
        WTW_13                      = 0x13
        WTW_14                      = 0x14
        WTW_15                      = 0x15
        SPI1_NOR                    = 0x16
        SPI1_NAND                   = 0x17

    #
    # Target device types (as specified in the loader's argument field)
    #
    class TargetType:
        SDRAM                       = 0
        SPI_NOR                     = 1
        SPI_NAND                    = 2
        SD_CARD                     = 3
        SPI_NOR_2                   = 4
        SPI_NOR_3                   = 5
        OTP                         = 7

    #-------------------------------------------#

    def flash_erase_block(self, addr):
        """ Erase flash block """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.ERASE_FLASH_BLOCK,
                            addr.to_bytes(4, 'big'))
        return resp[0]

    def flash_erase_sector(self, addr):
        """ Erase flash sector """
        self.cmd_exec(JL_LoaderV2.Cmd.ERASE_FLASH_SECTOR,
                            addr.to_bytes(4, 'big'))

    def flash_erase_chip(self):
        """ Erase flash chip """
        self.cmd_exec(JL_LoaderV2.Cmd.ERASE_FLASH_CHIP, b'')

    def read_status(self):
        """ Read status """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.READ_STATUS, b'')
        return resp[0]

    def flash_write(self, addr, data):
        """ Write flash """
        # Note: the CRC16 for data was only required for loaders prior to BR17 one (or something like that)
        self.cmd_exec_dataout(JL_LoaderV2.Cmd.WRITE_FLASH,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big')
                    + b'\x00' + jl_crc16(data).to_bytes(2, 'little'), data)

    def flash_read(self, addr, len):
        """ Read flash """
        return self.cmd_exec_datain(JL_LoaderV2.Cmd.READ_FLASH,
                addr.to_bytes(4, 'big') + len.to_bytes(2, 'big'), len)

    def mem_write(self, addr, data):
        """ Write memory """
        self.cmd_exec_dataout(JL_LoaderV2.Cmd.WRITE_MEMORY,
                addr.to_bytes(4, 'big') + len(data).to_bytes(2, 'big')
                    + b'\x00' + jl_crc16(data).to_bytes(2, 'little'), data)

    def mem_read(self, addr, len):
        """ Read memory """
        return self.cmd_exec_datain(JL_LoaderV2.Cmd.READ_MEMORY,
                addr.to_bytes(4, 'big') + len.to_bytes(2, 'big'), len)

    def mem_jump(self, addr, arg=0):
        """ Jump to memory """
        self.cmd_exec(JL_LoaderV2.Cmd.JUMP_TO_MEMORY,
                    addr.to_bytes(4, 'big') + arg.to_bytes(2, 'big'))

    def chip_key(self, arg=0xac6900):
        """ Read (chip)key """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.READ_KEY, arg.to_bytes(4, 'big'))
        return int.from_bytes(cipher_bytes(jl_crc_cipher, resp[4:6][::-1]), 'little')

    def online_device(self, _):
        """ Get online device """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.GET_ONLINE_DEVICE, b'')
        return {'type': resp[0], 'id': int.from_bytes(resp[2:6], 'little')}

    def read_id(self):
        """ Read ID """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.READ_ID, b'')
        return int.from_bytes(resp[0:3], 'big')

    def run_app(self, arg=1):
        """ Run app (or reset) """
        self.cmd_exec(JL_LoaderV2.Cmd.RUN_APP, arg.to_bytes(4, 'big'))

    def set_flash_cmds(self, cmds):
        """ Set flash commands, cmds in order:
            [0] = Chip erase command               (e.g. 0xC7)
            [1] = Block erase command              (e.g. 0xD8)
            [2] = Sector erase command             (e.g. 0x20)
            [3] = Read command                     (e.g. 0x03)
            [4] = Program command                  (e.g. 0x02)
            [5] = Read status register command     (e.g. 0x05)
            [6] = Write enable command             (e.g. 0x06)
            [7] = Write status register command    (e.g. 0x01)
        """

        self.cmd_exec_datain(JL_LoaderV2.Cmd.SET_FLASH_CMD,
            b'\x00\x00\x00\x00' + len(cmds).to_bytes(2, 'big'), bytes(cmds))

    def flash_crc16(self, addr, len):
        """ Calculate flash CRC16 (special) """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.FLASH_CRC16,
                        addr.to_bytes(4, 'big') + len.to_bytes(2, 'big'))
        return int.from_bytes(resp[:2], 'big')

    def write_chipkey(self, key, vpp=5000):
        """ Write (chip)key """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.WRITE_KEY,
                    key.to_bytes(4, 'big') + b'\x00' + vpp.to_bytes(4, 'big'))
        return int.from_bytes(resp[:4], 'big')

    def flash_crc16_raw(self, addr, len):
        """ Calculate flash CRC16 (raw) """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.FLASH_CRC16_RAW,
                        addr.to_bytes(4, 'big') + len.to_bytes(2, 'big'))
        return int.from_bytes(resp[:2], 'big')

    def usb_buffer_size(self):
        """ Get USB buffer size (aka get max flash page size) """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.GET_USB_BUFF_SIZE, b'')
        return int.from_bytes(resp[:4], 'big')

    def version(self):
        """ Get loader version """
        # Sometimes the cmd in response is wrong... FIXME
        rcmd, resp = self.cmd_exec(JL_LoaderV2.Cmd.GET_LOADER_VER, b'', check_response=False)
        return str(resp[1:5][::-1], 'ascii')

    def maskrom_id(self):
        """ Get MaskROM ID """
        resp = self.cmd_exec(JL_LoaderV2.Cmd.GET_MASKROM_ID, b'')
        return int.from_bytes(resp[:4], 'big')


class JL_UARTDevice:
    """
    Common base for UART protocols
    """

    def __init__(self, dev: Serial, is_jl_tool, single_wire=True):
        self.dev = dev
        self.single_wire = single_wire
        self.is_jl_tool = is_jl_tool

    def _read_impl(self, size):
        if self.is_jl_tool and self.dev.timeout is not None and self.dev.in_waiting < size:
            # The USB download tool driver appears to behave oddly and never
            # waits for timeout, so try to implement it in Python instead of
            # relying on the OS to do it
            target_time = time.time() + self.dev.timeout
            prev_in_waiting = self.dev.in_waiting
            while time.time() < target_time:
                curr_in_waiting = self.dev.in_waiting
                if prev_in_waiting != curr_in_waiting:
                    # Reset timer if data is coming in
                    target_time = time.time() + self.dev.timeout
                    prev_in_waiting = curr_in_waiting

                time.sleep(0.01)

        return self.dev.read(size)

    def read(self, size=1):
        return self._read_impl(size)

    def write(self, b):
        result = self.dev.write(b)
        if not self.single_wire:
            self._read_impl(len(b))
        return result


class JL_UARTBOOT(JL_UARTDevice):
    """
    JL UART initial loader protocol implementation
    """

    def send_loader(self, blob, load_addr, opt, rate, res=0, check_response=True):
        """Send loader binary"""
        # Create request
        # Magic bytes and blob-related fields
        blob_crc = jl_crc16(blob)
        req = struct.pack('<5sIIH', b'\x00\x55\xaa\x10\x20', load_addr, len(blob),
                          blob_crc)
        # Add CRC of blob-related fields and initial loader options
        req_crc = jl_crc16(req[5:])
        req += struct.pack('<HBBB', req_crc, opt, rate, res)
        # Add CRC of everything
        req += bytes([jl_crc8(req)])

        # Send request
        self.dev.read_all()
        self.write(req)
        resp = self.read(5)
        if resp != b'\x55\xaa\x01\x20\x22':
            return False

        # Switching baud rate here
        time.sleep(0.025)
        self.dev.baudrate = rate * 10000

        # Send payload
        self.write(blob)
        if check_response:
            resp = self.read(5)
            if resp != b'\x55\xaa\x01\x20\x22':
                return False

        return True

class JL_UARTLoader(JL_UARTDevice):
    """
    Loader class implementation for UART protocol.
    """

    #
    # Commands
    #
    class Cmd:
        INITIALIZE_FLASH            = 0xFC01
        CHIP_RESET                  = 0xFC02
        HANDSHAKE                   = 0xFC03
        STH_FC04                    = 0xFC04
        READ_CHIPID                 = 0xFC05
        READ_UID                    = 0xFC06
        STH_FC07                    = 0xFC07
        WRITE_KEY                   = 0xFC08
        READ_KEY                    = 0xFC09
        WRITE_EFUSE                 = 0xFC0A
        READ_EFUSE                  = 0xFC0B

        FLASH_CHIP_ERASE            = 0xFC11
        FLASH_BLOCK_ERASE           = 0xFC12
        FLASH_SECTOR_ERASE          = 0xFC13
        FLASH_PAGE_ERASE            = 0xFC14
        FLASH_GET_BLOCK_ALIGN       = 0xFC15

        FLASH_WRITE                 = 0xFC18
        FLASH_READ                  = 0xFC19
        FLASH_CHECKSUM_CHUNKS       = 0xFC1A
        FLASH_CHECKSUM_REGION       = 0xFC1B
        PIN_TEST                    = 0xFC1C

        FLASH_DISABLE_QPP           = 0xFC20

        STH_FC31                    = 0xFC31
        STH_FC32                    = 0xFC32

    MAX_CHUNK_LENGTH = 0x1000

    #-------------------------------------------#

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cipher_key = 0
        self.handshaked = False

    def crypt_buffer(self, buf):
        if self.handshaked:
            return cipher_bytes(jl_crc_cipher, buf, key=self.cipher_key)
        else:
            return buf

    def create_request(self, cmd, param_a=0, param_b=0, param_c=0, param_d=0, param_e=0, param_f=0):
        buf = struct.pack('>HIIHHBB', cmd, param_a, param_b, param_c, param_d, param_e, param_f)
        buf = jl_crc16(buf).to_bytes(2, 'big') + buf
        buf = self.crypt_buffer(buf)
        return buf

    def read_response_ex(self, cmd, length=20, ignore_marker=False):
        data = self.read(length)
        if len(data) != length:
            raise ValueError('Could not read response from device.')
        data = self.crypt_buffer(data)

        marker, crc, resp_cmd, status = struct.unpack('>HHHH', data[0:8])
        if not ignore_marker and marker != 0x00ff:
            raise ValueError('Marker mismatch')
        if crc != jl_crc16(data[4:]):
            raise ValueError('CRC mismatch')
        if resp_cmd != cmd:
            raise ValueError('Command mismatch')
        if status != 0:
            if status == 0x0016:
                raise ValueError('Request CRC check failed, or unknown command category')
            elif status == 0x0026:
                raise ValueError('Unknown command')
            else:
                raise ValueError('Received unknown status {}' % status)

        return data

    def read_response(self, cmd, length=20):
        data = self.read_response_ex(cmd, length)
        return data[8:]

    def read_chunk(self, length, has_data=True):
        read_len = 4
        if has_data:
            read_len += length
        data = self.read(read_len)
        if len(data) != read_len:
            raise ValueError('Failed to read complete chunk')
        data = self.crypt_buffer(data)

        crc, crc_inverted = struct.unpack('>HH', data[0:4])
        if crc != (crc_inverted ^ 0xffff):
            raise ValueError('CRC does not match complement')

        chunk = data[4:]
        if length != 0 and crc != jl_crc16(chunk):
            raise ValueError('CRC mismatch')

        return crc, chunk

    @property
    def supports_memory_commands(self):
        return False

    #-------------------------------------------#

    def initialize_flash(self, param=None):
        """Initialize flash"""
        if param is None:
            param = b'\x00' * 12
        else:
            param = param.encode()

        if len(param) != 12:
            raise ValueError('External flash parameter is not 12 characters in length.')

        pa, pb, pc, pd = struct.unpack('<IIHH', param)
        req = self.create_request(self.Cmd.INITIALIZE_FLASH, pa, pb, pc, pd)
        self.write(req)
        resp = self.read_response(self.Cmd.INITIALIZE_FLASH)
        return struct.unpack('>HI', resp[0:6])

    def online_device(self, flash_param):
        """ Get online device """
        # Shim for shell
        dev_type, dev_id = self.initialize_flash(flash_param)
        return {'type': dev_type, 'id': dev_id}

    def chip_reset(self):
        """Reset the chip"""
        req = self.create_request(self.Cmd.CHIP_RESET)
        self.write(req)
        # No response

    def run_app(self, _):
        """ Run app (or reset) """
        # Shim for shell
        self.chip_reset()
        raise Exception('Sent chip reset command')

    def handshake(self):
        """Perform handshake"""
        rand = random.randbytes(14)
        pa, pb, pc, pd, pe, pf = struct.unpack('<IIHHBB', rand)
        req = self.create_request(self.Cmd.HANDSHAKE, pa, pb, pc, pd, pe, pf)
        self.write(req)
        resp = self.read_response_ex(self.Cmd.HANDSHAKE, ignore_marker=True)

        if self.handshaked:
            crc_lo = ((self.cipher_key & 0xff) << 8) | ((self.cipher_key & 0xff00) >> 8)
        else:
            crc_lo = jl_crc16(req)
        crc_hi = jl_crc16(resp)

        self.cipher_key = ((crc_lo & 0xff) << 8) | ((crc_lo & 0xff00) >> 8) | \
            ((crc_hi & 0xff) << 24) | ((crc_hi & 0xff00) << 8)
        self.handshaked = True

    def sth_fc04(self):
        """Send command 0xFC04"""
        req = self.create_request(self.Cmd.STH_FC04)
        self.write(req)
        resp = self.read_response(self.Cmd.STH_FC04)
        return int.from_bytes(resp[0:4], 'big')

    def read_chip_id(self):
        """Read the microcontroller's chip ID"""
        req = self.create_request(self.Cmd.READ_CHIPID)
        self.write(req)
        resp = self.read_response(self.Cmd.READ_CHIPID)
        return int.from_bytes(resp[2:6], 'big')

    def read_uid(self):
        """Read the flash UID"""
        req = self.create_request(self.Cmd.READ_UID)
        self.write(req)
        return self.read_response(self.Cmd.READ_UID, 24)

    def write_chipkey(self, key):
        """Write (chip)key"""
        req = self.create_request(self.Cmd.WRITE_KEY)
        self.write(req)
        self.read_response(self.Cmd.WRITE_KEY)

    def chip_key(self):
        """Read (chip)key"""
        req = self.create_request(self.Cmd.READ_KEY)
        self.write(req)
        resp = self.read_response(self.Cmd.READ_KEY)
        return int.from_bytes(cipher_bytes(jl_crc_cipher, resp[0:2][::-1]), 'little')

    def write_efuse(self, value, safety, index):
        """Write value to eFuse"""
        req = self.create_request(self.Cmd.WRITE_EFUSE, value, safety, index)
        self.write(req)
        resp = self.read_response(self.Cmd.WRITE_EFUSE)
        return int.from_bytes(resp[0:4], 'big')

    def read_efuse(self, index):
        """Read value from eFuse"""
        req = self.create_request(self.Cmd.READ_EFUSE, param_c=index)
        self.write(req)
        resp = self.read_response(self.Cmd.READ_EFUSE)
        return int.from_bytes(resp[0:4], 'big')

    def flash_erase_chip(self):
        """Erase flash chip"""
        req = self.create_request(self.Cmd.FLASH_CHIP_ERASE)
        self.write(req)
        self.read_response(self.Cmd.FLASH_CHIP_ERASE)

    def flash_erase_block(self, addr):
        """Erase flash block"""
        req = self.create_request(self.Cmd.FLASH_BLOCK_ERASE, addr)
        self.write(req)
        self.read_response(self.Cmd.FLASH_BLOCK_ERASE)

    def flash_erase_sector(self, addr):
        """Erase flash sector"""
        req = self.create_request(self.Cmd.FLASH_SECTOR_ERASE, addr)
        self.write(req)
        self.read_response(self.Cmd.FLASH_SECTOR_ERASE)

    def flash_erase_page(self, addr):
        """Erase flash page"""
        req = self.create_request(self.Cmd.FLASH_PAGE_ERASE, addr)
        self.write(req)
        self.read_response(self.Cmd.FLASH_PAGE_ERASE)

    def flash_get_block_align(self):
        """Get block align"""
        req = self.create_request(self.Cmd.FLASH_GET_BLOCK_ALIGN)
        self.write(req)
        resp = self.read_response(self.Cmd.FLASH_GET_BLOCK_ALIGN)
        return int.from_bytes(resp[0:4], 'big')

    def flash_write(self, addr, data):
        """Write flash"""
        if len(data) > self.MAX_CHUNK_LENGTH:
            raise ValueError(f'Data length cannot be greater than {self.MAX_CHUNK_LENGTH} bytes')

        data_crc = jl_crc16(data)
        req = self.create_request(self.Cmd.FLASH_WRITE, addr, len(data), data_crc)
        self.write(req + data)
        self.read_response(self.Cmd.FLASH_WRITE)

    def flash_read(self, addr, length):
        """Read flash"""
        req = self.create_request(self.Cmd.FLASH_READ, addr, length)
        self.write(req)
        self.read_response(self.Cmd.FLASH_READ)

        data = bytearray()
        while length > 0:
            chunk_size = min(length, self.MAX_CHUNK_LENGTH)
            _, chunk = self.read_chunk(chunk_size)
            data.extend(chunk)
            length -= chunk_size

        return data

    def flash_crc16_chunks(self, addr, length):
        """Calculate flash CRC16 by chunks"""
        req = self.create_request(self.Cmd.FLASH_CHECKSUM_CHUNKS, addr, length)
        self.write(req)
        self.read_response(self.Cmd.FLASH_CHECKSUM_CHUNKS)

        crcs = []
        while length > 0:
            chunk_size = min(length, self.MAX_CHUNK_LENGTH)
            crc, _ = self.read_chunk(chunk_size, False)
            crcs.append(crc)
            length -= chunk_size

        return crcs

    def flash_crc16(self, addr, length):
        """Calculate flash CRC16"""
        req = self.create_request(self.Cmd.FLASH_CHECKSUM_REGION, addr, length)
        self.write(req)
        self.read_response(self.Cmd.FLASH_CHECKSUM_REGION)
        crc, _ = self.read_chunk(0, False)
        return crc

    def pin_test(self):
        """Perform pin test"""
        req = self.create_request(self.Cmd.PIN_TEST)
        self.write(req)
        resp = self.read_response(self.Cmd.PIN_TEST)
        return int.from_bytes(resp[2:6], 'big')

    def flash_disable_qpp(self):
        """Disable quad page programming"""
        req = self.create_request(self.Cmd.FLASH_DISABLE_QPP)
        self.write(req)
        self.read_response(self.Cmd.FLASH_DISABLE_QPP)
