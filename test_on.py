import asyncio
from pywizlight import wizlight

async def main():
    bulb = wizlight("192.168.86.123")
    await bulb.turn_on()
    print("Sent ON command")

asyncio.run(main())
