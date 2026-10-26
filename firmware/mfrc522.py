"""Minimal MFRC522 driver for the lane firmware.

Derived from the driver the bench test already used, reduced to the two
operations the firmware needs: request an idle card and read its anticollision
UID. It has no vendored dependency beyond ``machine`` and ``time``.

The RC522 is a 3.3 V part. Its SPI lines and supply must be 3.3 V.
"""

import time


class MFRC522:
    PCD_IDLE = 0x00
    PCD_TRANSCEIVE = 0x0C
    PCD_RESETPHASE = 0x0F

    PICC_REQIDL = 0x26
    PICC_ANTICOLL = 0x93

    CommandReg = 0x01 << 1
    ComIEnReg = 0x02 << 1
    ComIrqReg = 0x04 << 1
    ErrorReg = 0x06 << 1
    FIFODataReg = 0x09 << 1
    FIFOLevelReg = 0x0A << 1
    ControlReg = 0x0C << 1
    BitFramingReg = 0x0D << 1
    ModeReg = 0x11 << 1
    TxControlReg = 0x14 << 1
    TxASKReg = 0x15 << 1
    TModeReg = 0x2A << 1
    TPrescalerReg = 0x2B << 1
    TReloadRegL = 0x2C << 1
    TReloadRegH = 0x2D << 1

    def __init__(self, spi, rst, cs):
        self.spi = spi
        self.rst = rst
        self.cs = cs

        self.rst.value(0)
        time.sleep_ms(50)
        self.rst.value(1)
        time.sleep_ms(50)
        self.init()

    # --- register access ---------------------------------------------------

    def w_reg(self, reg, value):
        self.cs.value(0)
        self.spi.write(bytearray([(reg & 0x7E), value]))
        self.cs.value(1)

    def r_reg(self, reg):
        self.cs.value(0)
        self.spi.write(bytearray([((reg & 0x7E) | 0x80)]))
        value = self.spi.read(1)
        self.cs.value(1)
        return value[0]

    def set_bit_mask(self, reg, mask):
        self.w_reg(reg, self.r_reg(reg) | mask)

    def clear_bit_mask(self, reg, mask):
        self.w_reg(reg, self.r_reg(reg) & (~mask))

    # --- reader setup ------------------------------------------------------

    def antenna_on(self):
        if (self.r_reg(self.TxControlReg) & 0x03) != 0x03:
            self.set_bit_mask(self.TxControlReg, 0x03)

    def init(self):
        self.w_reg(self.CommandReg, self.PCD_RESETPHASE)
        self.w_reg(self.TModeReg, 0x80)
        self.w_reg(self.TPrescalerReg, 0xA9)
        self.w_reg(self.TReloadRegL, 0x30)
        self.w_reg(self.TReloadRegH, 0x0)
        self.w_reg(self.TxASKReg, 0x40)
        self.w_reg(self.ModeReg, 0x3D)
        self.antenna_on()

    # --- transceive --------------------------------------------------------

    def to_card(self, command, send_data):
        back_data = []
        back_len = 0
        status = False

        self.w_reg(self.ComIEnReg, 0x77 | 0x80)
        self.clear_bit_mask(self.ComIrqReg, 0x80)
        self.w_reg(self.CommandReg, self.PCD_IDLE)
        self.set_bit_mask(self.FIFOLevelReg, 0x80)

        for value in send_data:
            self.w_reg(self.FIFODataReg, value)

        self.w_reg(self.CommandReg, command)
        self.set_bit_mask(self.BitFramingReg, 0x80)

        # Bounded wait for the command to finish or fail. This is a busy loop
        # because a card response is expected within milliseconds.
        guard = 2000
        irq = 0
        while True:
            irq = self.r_reg(self.ComIrqReg)
            guard -= 1
            if guard == 0 or irq & 0x01 or irq & 0x30:
                break

        self.clear_bit_mask(self.BitFramingReg, 0x80)

        if guard != 0:
            if not (self.r_reg(self.ErrorReg) & 0x1B):
                status = True
                count = self.r_reg(self.FIFOLevelReg)
                last_bits = self.r_reg(self.ControlReg) & 0x07
                if last_bits != 0:
                    back_len = (count - 1) * 8 + last_bits
                else:
                    back_len = count * 8
                count = max(1, min(count, 16))
                for _ in range(count):
                    back_data.append(self.r_reg(self.FIFODataReg))

        return status, back_data, back_len

    def request(self, req_mode):
        """Return ``(card_present, bit_length)``."""
        self.w_reg(self.BitFramingReg, 0x07)
        status, _, back_len = self.to_card(self.PCD_TRANSCEIVE, [req_mode])
        return (status and back_len == 0x10), back_len

    def anticoll(self):
        """Return ``(ok, uid_bytes)`` for the first card in the field."""
        self.w_reg(self.BitFramingReg, 0x00)
        status, back_data, _ = self.to_card(
            self.PCD_TRANSCEIVE, [self.PICC_ANTICOLL, 0x20]
        )
        if status and len(back_data) == 5:
            checksum = (
                back_data[0] ^ back_data[1] ^ back_data[2] ^ back_data[3]
            )
            if checksum == back_data[4]:
                return True, back_data[:4]
        return False, []

    def read_uid(self):
        """Return an uppercase hex UID string, or None when no card answers.

        Only 4-byte UIDs are returned by ``anticoll``. That matches the tags
        used on the bench. A 7-byte NTAG would need the cascade-level request;
        the backend accepts 4–32 hex characters, but this driver does not yet
        read the second cascade level.
        """
        present, _ = self.request(self.PICC_REQIDL)
        if not present:
            return None
        ok, uid = self.anticoll()
        if not ok:
            return None
        return "".join("%02X" % value for value in uid)
