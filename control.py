import asyncio
from pywizlight import wizlight, PilotBuilder

async def main():
    bulb = wizlight("192.168.86.123")
    await bulb.turn_on(PilotBuilder(brightness=30))
    print("Done")

asyncio.run(main())
