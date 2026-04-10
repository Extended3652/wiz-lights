#!/home/pi/venvs/wiz/bin/python

import asyncio
from pywizlight import wizlight

BULBS = [
    "192.168.86.123",
    "192.168.86.124",
]

async def main():
    lights = [wizlight(ip) for ip in BULBS]

    for light in lights:
        await light.turn_off()

    print("Both bulbs off")

asyncio.run(main())
