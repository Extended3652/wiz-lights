import asyncio
from pywizlight import wizlight

BULBS = [
    "192.168.86.123",
    "192.168.86.124",
]

async def main():
    for ip in BULBS:
        await wizlight(ip).turn_off()

    print("Both bulbs off")

asyncio.run(main())
