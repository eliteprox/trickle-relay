#!/usr/bin/env python3
"""
Example: Using WHEP Client Programmatically
"""

import asyncio
import logging
from whep_to_comfystream_relay import WHEPToComfyStreamRelay

logging.basicConfig(level=logging.INFO)

async def main():
    # Create relay instance
    relay = WHEPToComfyStreamRelay()

    try:
        # Start relay session
        session_id = await relay.start_relay(
            whep_endpoint_url="http://localhost:7088/whip/endpoint/test",
            comfystream_url="http://localhost:8889",
            prompts=[{"text": "enhance this video with AI"}],
            bearer_token=None,  # Add token if needed
            width=512,
            height=512
        )

        print(f"✅ Started session: {session_id}")

        # Monitor for 30 seconds
        for i in range(3):
            await asyncio.sleep(10)
            status = relay.get_session_status(session_id)
            print(f"Status: {status['status']}, Queues: {status['queue_sizes']}")

        # Stop session
        await relay.stop_relay(session_id)
        print("✅ Session stopped")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
