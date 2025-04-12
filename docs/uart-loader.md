# UART loader protocol

The UART loader is available on some chips, and mandatory for those that do not
have a USB interface. To use a UART loader, it needs to be uploaded into memory
as outlined in the [upload process](uart-protocol.md).

The information in this document is based on the UART loader for SH55. There
may be unique features or omissions compared to other UART loaders.

## Initialization

When downloading the loader, append the binary version of `isd_config.ini` to
the end of the loader blob. Encrypt the binary INI in such a way that it remains
encrypted with the `CrcDecode` cipher when the loader is ready to execute, i.e.
if the loader encryption flag is set, do not encrypt the binary INI, but if it
is not set, do encrypt the binary INI. The loader will decrypt the INI before
it is read.

The binary INI must start with `chipkey.bin` and its CRC, but this does not need
to match what's burned into the chip or even be valid. Following that are
the serialized INI contents, which are from the `SYS_CFG_PARAM` section of the
original `isd_config.ini` you'd usually use with the ISD download tool. The
following keys are read:

* `SPI`: SPI flash configuration. It is in this format:
  ```
  <data_width>_<clk>_<mode>
  ```
  * `<data_width>`: SPI data width, one of these values:
    * `0`: half-duplex 3-wire interface
    * `1`: full-duplex 4-wire interface
    * `2`: dual IO
    * `4`: quad IO
  * `<clk>`: SPI baud divisor, nominally `0` to `9`, but could be any 7-bit
    value as long as you add the ASCII value of `0` (48) to it
  * `<mode>`: Read mode, one of these values:
    * `0`: one line for command, one line for addresses and data
    * `1`: one line for command, many lines for addresses and data
    * `2`: use continuation read, many lines for addresses and data; used with
      flash chips that support multi-IO performance enhance mode
* `RESET`: software reset pin configuration. It is in this format:
  ```
  <pin>_<reset_time>_<reset_level>
  ```
  * `<pin>`: pin definition, see below
  * `<reset_time>`: activation seconds, valid values are `0`, `4`, and `8`,
    where `0` is disabled
  * `<reset_level>`: high or low logic level to activate timeout; `0` for low,
    `1` for high

  Note that this setting is parsed, but doesn't appear to actually be used
  within the loader
* `EX_FLASH_IO`: secondary flash configuration. In the original `isd_config.ini`,
  pins for each signal may be specified, but in the SH55 loader only the data
  width is supported, as documented above. Stored as a string, not a number
* `LATCH_DLY_RELEASE`: exact purpose unknown. This value does not seem to be
  documented, but can be set to `0` or `1` (stored as a number). When `1`,
  the value `0` is written to P33 register `0xaf` (`P3_ANA_LAT`)

In the original command sent to the ROM, the baud rate setting is used to
reinitialize the UART interface. No other fields are read from that command.
As with the ROM, the value is multiplied by 10000 for the final setting.
If the baud rate multiplier was set to `0`, the ROM will have replaced it
with `10` (for 100000 baud).

After the loader has initialized, it will send an acknowledgement sequence of
bytes `55 aa 01 20 22` to indicate it is ready to receive commands.

### Pin definition

A pin definition can contain a pin number or USB pin.

#### Pin number

Pin number can usually be 3 or 4 characters, subject to how each caller
implements parsing. 4-character pin numbers are usually the norm, and is
identified by a `P` prefix. 3-character pin numbers do not have a `P` prefix.
The port is specified, from `A` to `H`, followed by the pin index from `00` to
`15`.

Example: `PB04` is translated to pin number 36, as each port has 16 pins, so
port `B` starts at number 32, and adding pin index 4 results in pin number 36.
`B04` is an equivalent 3-character definition.

There is a check for port `R`, which causes the pin number base to be 82.
However, this only works for 4-character definitions, and it is unclear if this
was intentional.

#### USB pins

You can also specify `DP` or `USBDP` to specify pin number 61, or `DM` or
`USBDM` to specify pin number 62. You can use `DP`/`DM` in 3 or 4-character
definitions by padding to the required length with any characters, but you
cannot fit `USBDP`/`USBDM` in a smaller space. Technically any second character
that is not a `P` when the first character is `D` will map to `DM`.

## Communication format

The loader uses fixed-length requests and responses, which become encrypted
after the host sends a handshake command. All data is sent in big-endian format,
however there are internal conversions to change data to little-endian, which
may affect how some data is sent on the line, and will be pointed out as needed.

### Request format

Each request is a fixed 18-byte message, in this layout:

```
HH:hh fc RR AA:aa:aa:aa BB:bb:bb:bb CC:cc DD:dd EE FF
```

* `HH:hh`: CRC16 checksum of the next 16 bytes
* `fc`: category, always fixed
* `RR`: command code
* `AB:aa:aa:aa`: parameter A, 32-bit
* `BB:bb:bb:bb`: parameter B, 32-bit
* `CC:cc`: parameter C, 16-bit
* `DD:dd`: parameter D, 16-bit
* `EE`: parameter E, 8-bit
* `FF`: parameter F, 8-bit

Request parameters are generally packed within the request itself, aside from
certain commands like flash write, which may send its payload appended
(unencrypted) to the original request, which can be `0x1000` bytes at most.

### Response format

Each response is a fixed 20-byte message (with a few exceptions), in this
layout:

```
00:ff HH:hh fc RR SS:ss|xx xx xx xx xx xx xx xx xx xx xx xx
```

* `00:ff`: fixed bytes to indicate this is a response
* `HH:hh`: CRC16 checksum of the next 16 bytes
* `fc`: category from request
* `RR`: command code from request
* `SS:ss`: command status: command parsing status, not command result. Can be
  the following:
  * `00:00`: success
  * `00:16`: CRC check failed, unknown category
  * `00:26`: unknown command (including locked commands before handshake)

  Commands will usually return `00:00` although the operation may not have
  succeeded.
* `xx xx ...`: command result, format varies with each command

Command results are generally packed within the response itself, aside from
certain commands like flash read and calculate flash checksum, where
separately encrypted chunks may be sent, with each block being at most `0x1004`
bytes (including checksums).

Unused bytes in the response are filled with the lower 8 bits read from the
`TMR0_CNT` register.

### Encryption

Encryption of requests and responses are performed using the `CrcDecode` cipher
with a custom key after the host performs a handshake. Before a handshake has
been completed, the host cannot issue any commands other than a handshake.

When the handshake is requested (command `0x03`), the lower 16 bits of the key
is set to the endian-flipped CRC16 of the whole request data. The loader sends
back a response, and the upper 16 bits of the key is set to the endian-flipped
CRC16 of the whole response data. After this, encryption is enabled and all
requests must be sent encrypted, and all responses and corresponding data
will be sent encrypted. It is possible to perform a new handshake after the
initial handshake, in which case the request needs to be encrypted with the
current key, and after sending the response encrypted with the current key, the
loader will update the upper 16 bits of the key as previously mentioned.

For requests, the additional payload is unencrypted. For responses, additional
chunks are encrypted.

## Commands

### `0x01`: Initialize SPI NOR flash

If an `EX_FLASH`-formatted parameter is provided, flash access is set up
accordingly. Otherwise, internal/default flash settings are used. The flash
chip selected will have its status register set to `0x00` (effectively
removing write protection from all blocks), and the flash ID is returned.

#### Parameters

**Note:** this command takes parameters in a non-conventional way. It
interprets the entire parameters section of the request as a single string, but
the data is still subject to endianess conversion, so be sure to flip the
endian of parameters A, B, C, and D before calculating the checksum and sending
the command.

```
<cs_pin>_<spi><port>_<power_io_data_width>
```

* `<cs_pin>`: pin definition for the flash's CS pin. This must take up 4
  characters of space (typically 4-character pin name, but you can use a
  3-character name with an extra character of padding)
* `<spi>`: the SPI peripheral index to use. E.g. `0` indicates `SPI0`, `1`
  indicates `SPI1`
* `<port>`: port mapping to use. See `SPIx_IOS` in the IOMC of the respective
  chip's chip manual. Valid values are `A` to `D`
* `<power_io_data_width>`: a combination of power pin definition and data width.
  If data width needs to be changed, use a 3-character pin name followed by the
  selected width. If data width does not need to be changed since last set (the
  default setting is `0`), use a 4-character pin name. If flash power is not
  controlled by the microcontroller, write `NULL` or `NUL` (strictly speaking
  only the first `N` is checked; length requirements still apply)

  Data width values are the same as in the `SPI` configuration from the binary
  INI.

You cannot select `USBDP` or `USBDM` for the pin definitions, but technically
you can use `DP` and `DM` with sufficient padding to select them.

To use the built-in flash, don't send a string in the parameters. Specifically,
the `_` at index 4 (after where the CS pin definition would be) must not be
present.

Examples:

* `PA12_1A_NULL`: CS is on pin `PA12`, `SPI1` is used, and port set `A` is used.
  There is no power pin configured, and the data width is not changed.
* `PA05_1B_PA00`: CS is on pin `PA05`, `SPI1` is used, and port set `B` is used.
  Power pin is on `PA00`, and the data width is not changed.
* `PA05_1B_NUL2`: CS is on pin `PA05`, `SPI1` is used, and port set `B` is used.
  There is no power pin configured, and the data width is set to dual IO.
* `PA05_1B_A002`: CS is on pin `PA05`, `SPI1` is used, and port set `B` is used.
  ower pin is on `PA00`, and the data width is set to dual IO.

#### Response

```
|AA:aa BB:bb:bb:bb -- -- -- -- -- --
```
* `AA:aa`: target memory, currently only known to return `3` (likely for SPI
  NOR flash)
* `BB:bb:bb:bb`: SPI flash ID, lower three bytes are response from `RDID`
  command issued to the flash

### `0x02`: Chip reset

Immediately resets the chip. Does not take parameters or return a response.

### `0x03`: Handshake

Performs handshake, sets encryption keys based on request and response, and
enables encryption on future requests and responses.

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

### `0x04`: Unknown

Does not seem to do anything, but does return a fixed result. Perhaps returns
loader version?

#### Response

```
|AA:aa:aa:aa -- -- -- -- -- -- -- --
```

* `AA:aa:aa:aa`: the value `0`

### `0x05`: Read chip ID

Returns chip ID from the `CHIP_ID` register.

#### Response

```
|-- -- AA:aa:aa:aa -- -- -- -- -- --
```

* `AA:aa:aa:aa`: the chip ID

### `0x06`: Read flash unique ID

Returns the flash's unique ID through the `RUID` command. The start of the ID
may be chopped off, and the CRC may not be correct.

#### Response

**Note:** this response is 24 bytes in length including non-result bytes to
accomodate the full UID, which is 16 bytes in length.

```
|AA:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa:aa
```

* `AA:...`: UID read from flash

### `0x07`: Not implemented

Although this command is supported, it does not have any side effects nor send
a response.

### `0x08`: Write chipkey

Writes chipkey to the lower 16 bits of eFuse(1), trying up to 5 times.

#### Parameters

* Parameter A: the key to write. Only the lower 16 bits are used

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

### `0x09`: Read chipkey

Reads chipkey from eFuse(1).

#### Response

```
|AA:aa -- -- -- -- -- -- -- -- -- --
```

* `AA:aa`: the chipkey, encrypted with `CrcDecode`, default key

### `0x0a`: Write eFuse

Writes value to selected eFuse

#### Parameters

* Parameter A: the value to write
* Parameter B: safety value, needs to be less than the value returned in the
  response
* Parameter C: eFuse index, only lower byte used

#### Response

```
|AA:aa:aa:aa -- -- -- -- -- -- -- --
```

* `AA:aa:aa:aa`: max safety value, parameter B needs to be less than this

### `0x0b`: Read eFuse

Reads value from selected eFuse

#### Parameters

* Parameter C: eFuse index, only lower byte used

Previous parameters are ignored.

#### Response

```
|AA:aa:aa:aa -- -- -- -- -- -- -- --
```

* `AA:aa:aa:aa`: the eFuse's value

### `0x11`: Flash chip erase

Erases the main memory of the flash chip.

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

### `0x12`: Flash block erase

Erases the block at the provided address on the flash chip.

#### Parameters

* Parameter A: address to erase block at

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

### `0x13`: Flash sector erase

Erases the sector at the provided address on the flash chip.

#### Parameters

* Parameter A: address to erase sector at

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

### `0x14`: Flash page erase

Erases the page at the provided address on the flash chip.

#### Parameters

* Parameter A: address to erase page at

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

### `0x15`: Flash get block align

Retrieves the block align value using the modulus in the firmware header. If the
firmware header fails CRC check, or the block align modulus is not `16` or `1`,
the loader tries to figure it out automatically by writing, erasing, and
comparing the first page of flash.

#### Response

```
|AA:aa:aa:aa -- -- -- -- -- -- -- --
```

* `AA:aa:aa:aa`: block align value

### `0x18`: Flash write

Writes payload to flash.

#### Parameters

* Parameter A: address to write payload to
* Parameter B: length of the payload
* Parameter C: CRC16 of the payload

Following the request bytes is the payload, unencrypted. The payload can be at
most `0x1000` bytes.

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

### `0x19`: Flash read

Reads data from flash.

#### Parameters

* Parameter A: address to read data from
* Parameter B: length of the data to read

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

For each `0x1000` chunk of data read:

```
AA:aa BB:bb xx:xx:xx...
```

* `AA:aa`: the CRC16 of the data in this chunk
* `BB:bb`: the inverse CRC16 (xor `0xffff`)
* `xx:xx:xx...`: the chunk data (up to `0x1000` bytes)

### `0x1a`: Flash checksum by chunk

Returns checksum for each chunk read from flash.


#### Parameters

* Parameter A: address to read data from
* Parameter B: length of the data to read

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

For each `0x1000` chunk of data read:

```
AA:aa BB:bb
```

* `AA:aa`: the CRC16 of the data in this chunk
* `BB:bb`: the inverse CRC16 (xor `0xffff`)

### `0x1b`: Flash checksum whole region

Returns checksum for the flash region read.


#### Parameters

* Parameter A: address to read data from
* Parameter B: length of the data to read

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

Response is sent immediately upon request reception.

After the data has been read:

```
AA:aa BB:bb
```

* `AA:aa`: the CRC16 of the data in the region
* `BB:bb`: the inverse CRC16 (xor `0xffff`)

### `0x1c`: Pin test

Performs some testing. On SH55, the MPWM pins are tested.

| Drive pin | Drive pin configuration      | Check pin | Check pin configuration | Delay | Check pin result |
|-----------|------------------------------|-----------|-------------------------|-------|------------------|
| PD3       | output low, no pull, digital | PA11      | input, pull up, digital | 100µs | high             |
| PD3       | output low, no pull, digital | PA12      | input, pull up, digital | 100µs | high             |
| PD0       | output low, no pull, digital | PA7       | input, pull up, digital | 100µs | high             |
| PD1       | output low, no pull, digital | PA6       | input, pull up, digital | 100µs | high             |

#### Response

```
|-- -- Aa:aa:aa:aa -- -- -- -- -- --
```

* `AA:aa:aa:aa`: result. `0` if successful, `1` if failure

### `0x20`: Disable quad page programming

Disables quad page programming mode on flash. Only applies to Puya
P25Q64/128(S)H.

#### Response

```
|-- -- -- -- -- -- -- -- -- -- -- --
```

### `0x31`: Not implemented

Although this command is supported, it does not have any side effects nor send
a response.

### `0x32`: Not implemented

Although this command is supported, it does not have any side effects nor send
a response.
