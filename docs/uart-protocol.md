# UART protocol

After the bootrom enters UART boot mode either by entry using the UART key,
having no valid app to boot, or requested by the application, it will listen for
a command to download the UART loader at 9600 baud.

## Download loader command

The download loader command is the only command accepted in this state, and has
the following structure:

```
00:55:aa:10:20 aa:aa:aa:AA ll:ll:ll:LL pp:PP mm:MM OO BB RR WW
```

* `00:55:aa:10:20`: fixed magic bytes; specifically, only the `55:aa` must be
  present and at the same position, as the bootrom verifies this and uses it
  to locate the position of the message in the DMA read buffer if DMA is in use.
  The other bytes can be any value.
* `aa:aa:aa:AA`: the address to load the payload to
* `ll:ll:ll:LL`: the length of the payload
* `pp:PP`: CRC16 of the payload
* `mm:MM`: CRC16 of all data after the fixed bytes and before this field, i.e.
  address, payload length, and payload CRC
* `OO`: loader option byte; bootrom will check bit 1 (`0x02`). If set, it will
  decrypt the payload after reception using `CrcDecode`
* `BB`: baud rate used for subsequent communication. It is multiplied by 10000,
  i.e. if `10` is specified, the baud rate will be 100000. This is used when
  sending the payload, along with any communications from the loader. If `0` is
  specified, the bootrom will automatically replace it with `10`.
* `RR`: reserved value, unused
* `WW`: CRC8 of all bytes before the current field; not checked by bootrom

All fields are in little-endian, which is different from the byte order that the
loader uses.

After the bootrom receives and validates the message, it responds with the bytes
`55:aa:01:20:22`.

## Payload sending

After receiving response from the device, switch to the baud rate that was
specified in the command and send the loader payload. When the loader has been
received, its checksum will be verified, and the data decrypted if required. Note
that the checksum is verified before decryption, not after. Finally, the bootrom
jumps to the start of the payload in RAM.

## Loader considerations

These points are specific to the loader, and are not handled by the bootrom.

You can attach parameters in the form of the `isd_config.ini` binary found
inside firmware images. It consists of a 32-byte chipkey blob, CRC16 of the
chipkey, and a list of binary-serialized key-value pairs. The extra data can
be up to 1024 bytes in length, depending on the available RAM on the chip. The
data is expected to be encrypted at the time the loader runs, so if encryption
of the loader payload is indicated, the appended data should not be encrypted,
because the bootrom will try to decrypt it after reception, and cause the data
to become encrypted in memory, as desired.

After the loader has initialized, it will respond with the same `55:aa:01:20:22`
but at the current baud rate. This indicates the loader is ready to receive
commands. For information on the loader protocol, see the
[UART loader protocol](uart-loader.md) document.
