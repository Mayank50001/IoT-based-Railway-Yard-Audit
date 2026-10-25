import re

from django.core.exceptions import ValidationError

SEPARATORS = re.compile(r'[\s:\-]')
HEX_ONLY = re.compile(r'^[0-9A-F]+$')


def canonical_uid(raw):
    """Return the canonical uppercase-hex form of an RFID UID.

    Examples: 'fa:37:e6:3f' -> 'FA37E63F', '0e:78:e1:3f' -> '0E78E13F'.
    Leading zeros are preserved; separators are removed.
    """
    if raw is None:
        raise ValidationError('RFID UID is required.')

    text = SEPARATORS.sub('', str(raw)).upper()

    if not text:
        raise ValidationError('RFID UID is required.')
    if not HEX_ONLY.match(text):
        raise ValidationError(
            'RFID UID must be hexadecimal (0-9, A-F); got %(raw)r.',
            params={'raw': raw},
        )
    if len(text) % 2 or not 4 <= len(text) <= 32:
        raise ValidationError(
            'RFID UID must be 4 to 32 hex characters in whole bytes.'
        )

    return text
