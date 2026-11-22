# SPDX-License-Identifier: GPL-3.0-or-later
"""Television adapters. Authenticated device protocols are not implemented yet."""
from .base import BaseAdapter, register


class _UnsupportedTelevision(BaseAdapter):
    async def list_capabilities(self):
        return []

    async def write_capability(self, capability, value):
        raise NotImplementedError(f'{self.protocol} control requires a supported paired client')


@register
class SamsungTvAdapter(_UnsupportedTelevision):
    protocol = 'samsung_tv'


@register
class LgTvAdapter(_UnsupportedTelevision):
    protocol = 'lg_tv'


@register
class VizioTvAdapter(_UnsupportedTelevision):
    protocol = 'vizio_tv'
