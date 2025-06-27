#!/usr/bin/env python3
"""
WHEP to ComfyStream Relay

A WHEP (WebRTC-HTTP Egress Protocol) client that subscribes to a WHIP endpoint,
receives WebRTC media streams, and forwards them to ComfyStream for AI processing.

This script focuses only on the consumption/forwarding aspect - no publishing yet.

Flow:
1. Connect to WHIP endpoint via WHEP protocol
2. Receive WebRTC media stream
3. Forward frames to ComfyStream via WebRTC
4. Process frames (currently just logging/monitoring)

This is separate from the trickle relay and uses WHEP protocol instead.
"""

import asyncio
import aiohttp
import json
import logging
import uuid
import time
import argparse
from typing import Dict, Optional, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# WebRTC imports
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
    MediaStreamTrack
)

logger = logging.getLogger(__name__)

@dataclass
class WHEPSession:
    """Represents a WHEP client session"""
    session_id: str
    whep_endpoint_url: str
    comfystream_url: str = "http://localhost:8889"
    whep_resource_url: Optional[str] = None
    prompts: List[Dict[str, Any]] = field(default_factory=lambda: [{"text": "enhance this video stream"}])
    width: int = 512
    height: int = 512
    status: str = 'starting'
    created_at: datetime = field(default_factory=datetime.now)

    # WebRTC connections
    whep_peer_connection: Optional[RTCPeerConnection] = None
    comfystream_peer_connection: Optional[RTCPeerConnection] = None
    comfystream_data_channel: Optional[Any] = None

    # Queues for frame processing
    received_frame_queue: Optional[asyncio.Queue] = None
    processed_frame_queue: Optional[asyncio.Queue] = None

class ForwardingVideoTrack(MediaStreamTrack):
    """Video track that forwards frames from WHEP to ComfyStream"""

    kind = "video"

    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True
        self.frame_count = 0

    async def recv(self):
        """Get next frame to send to ComfyStream"""
        try:
            if not self._running:
                raise Exception("Track stopped")

            # Get frame from WHEP stream
            frame = await self.frame_queue.get()

            if frame is None:
                self._running = False
                raise Exception("End of stream")

            self.frame_count += 1
            if self.frame_count % 100 == 0:
                logger.info(f"Forwarded {self.frame_count} frames to ComfyStream")

            return frame

        except Exception as e:
            logger.error(f"ForwardingVideoTrack recv error: {e}")
            raise

    def stop(self):
        self._running = False

class WHEPClient:
    """WHEP client for subscribing to WebRTC streams"""

    def __init__(self):
        self.session = aiohttp.ClientSession()

    async def subscribe(self, whep_endpoint_url: str, bearer_token: Optional[str] = None) -> Tuple[RTCPeerConnection, str]:
        """Subscribe to a WHEP endpoint using the WHEP protocol"""
        try:
            # Create peer connection for receiving media
            configuration = RTCConfiguration(
                iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")]
            )
            pc = RTCPeerConnection(configuration)

            # Add transceivers for receiving media
            pc.addTransceiver("audio", direction="recvonly")
            pc.addTransceiver("video", direction="recvonly")

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Prepare headers
            headers = {"Content-Type": "application/sdp", "Accept": "application/sdp"}
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"

            logger.info(f"Sending WHEP request to {whep_endpoint_url}")

            # Send WHEP request
            async with self.session.post(
                whep_endpoint_url, data=pc.localDescription.sdp, headers=headers
            ) as response:
                if response.status != 201:
                    error_text = await response.text()
                    raise Exception(f"WHEP request failed: {response.status} - {error_text}")

                whep_resource_url = response.headers.get("Location")
                if not whep_resource_url:
                    raise Exception("WHEP response missing Location header")

                sdp_answer = await response.text()

            # Set remote description
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp_answer, type="answer"))

            logger.info(f"WHEP connection established, resource: {whep_resource_url}")
            return pc, whep_resource_url

        except Exception as e:
            logger.error(f"WHEP subscription error: {e}")
            raise

    async def terminate_session(self, whep_resource_url: str, bearer_token: Optional[str] = None):
        """Terminate WHEP session by sending HTTP DELETE"""
        try:
            headers = {}
            if bearer_token:
                headers["Authorization"] = f"Bearer {bearer_token}"

            async with self.session.delete(whep_resource_url, headers=headers) as response:
                if response.status == 200:
                    logger.info("WHEP session terminated successfully")
                else:
                    logger.warning(f"WHEP termination returned status {response.status}")
        except Exception as e:
            logger.error(f"Error terminating WHEP session: {e}")

    async def close(self):
        """Close the WHEP client"""
        if self.session:
            await self.session.close()

class ComfyStreamClient:
    """WebRTC client for connecting to ComfyStream"""

    def __init__(self, comfystream_url: str):
        self.comfystream_url = comfystream_url
        self.session = aiohttp.ClientSession()

    async def create_connection(self, prompts: List[Dict[str, Any]], width: int, height: int,
                              input_track: ForwardingVideoTrack,
                              output_queue: asyncio.Queue) -> Tuple[RTCPeerConnection, Any]:
        """Create WebRTC connection to ComfyStream"""
        try:
            configuration = RTCConfiguration(
                iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")]
            )
            pc = RTCPeerConnection(configuration)

            # Add input track (frames from WHEP)
            pc.addTrack(input_track)

            # Create data channel for control messages
            data_channel = pc.createDataChannel("control")

            @pc.on("track")
            def on_track(track):
                logger.info(f"Received processed track from ComfyStream: {track.kind}")
                if track.kind == "video":
                    # Process incoming processed frames
                    async def process_incoming_frames():
                        processed_count = 0
                        try:
                            while True:
                                frame = await track.recv()
                                await output_queue.put(frame)
                                processed_count += 1
                                if processed_count % 50 == 0:
                                    logger.info(f"Received {processed_count} processed frames from ComfyStream")
                        except Exception as e:
                            logger.error(f"Error receiving processed frames: {e}")

                    asyncio.create_task(process_incoming_frames())

            @data_channel.on("open")
            def on_data_channel_open():
                logger.info("ComfyStream data channel opened")

                # Send resolution update
                resolution_msg = {
                    "type": "update_resolution",
                    "width": width,
                    "height": height
                }
                data_channel.send(json.dumps(resolution_msg))
                logger.info(f"Sent resolution update to ComfyStream: {width}x{height}")

            @data_channel.on("message")
            def on_data_channel_message(message):
                logger.debug(f"ComfyStream control message: {message}")

            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Send offer to ComfyStream
            offer_data = {
                "prompts": prompts,
                "offer": {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                }
            }

            logger.info(f"Sending WebRTC offer to ComfyStream: {self.comfystream_url}/offer")

            async with self.session.post(
                f"{self.comfystream_url}/offer",
                json=offer_data,
                headers={"Content-Type": "application/json"}
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"ComfyStream offer failed: {response.status} - {error_text}")

                answer_data = await response.json()

            # Set remote description
            answer = RTCSessionDescription(
                sdp=answer_data["sdp"],
                type=answer_data["type"]
            )
            await pc.setRemoteDescription(answer)

            logger.info("ComfyStream WebRTC connection established")
            return pc, data_channel

        except Exception as e:
            logger.error(f"Error creating ComfyStream connection: {e}")
            raise

    async def close(self):
        """Close the ComfyStream client"""
        if self.session:
            await self.session.close()

class WHEPToComfyStreamRelay:
    """Main relay service that connects WHEP to ComfyStream"""

    def __init__(self):
        self.active_sessions: Dict[str, WHEPSession] = {}

    async def start_relay(self, whep_endpoint_url: str, comfystream_url: str = "http://localhost:8889",
                         prompts: Optional[List[Dict[str, Any]]] = None, bearer_token: Optional[str] = None,
                         width: int = 512, height: int = 512) -> str:
        """Start a new WHEP to ComfyStream relay session"""

        session_id = str(uuid.uuid4())

        if prompts is None:
            prompts = [{"text": "enhance this video stream"}]

        session = WHEPSession(
            session_id=session_id,
            whep_endpoint_url=whep_endpoint_url,
            comfystream_url=comfystream_url,
            prompts=prompts,
            width=width,
            height=height
        )

        # Initialize frame queues
        session.received_frame_queue = asyncio.Queue()
        session.processed_frame_queue = asyncio.Queue()

        # Store session
        self.active_sessions[session_id] = session

        try:
            # Step 1: Subscribe to WHEP endpoint
            whep_client = WHEPClient()
            whep_pc, whep_resource_url = await whep_client.subscribe(
                whep_endpoint_url, bearer_token
            )

            session.whep_peer_connection = whep_pc
            session.whep_resource_url = whep_resource_url

            # Set up WHEP track handlers
            @whep_pc.on("track")
            def on_whep_track(track):
                logger.info(f"Received WHEP track: {track.kind}")
                if track.kind == "video" and session.received_frame_queue:
                    # Forward received frames to ComfyStream queue
                    async def forward_whep_frames():
                        frame_count = 0
                        try:
                            while True:
                                frame = await track.recv()
                                if session.received_frame_queue:
                                    await session.received_frame_queue.put(frame)
                                frame_count += 1
                                if frame_count % 100 == 0:
                                    logger.info(f"Received {frame_count} frames from WHEP")
                        except Exception as e:
                            logger.error(f"Error receiving WHEP frames: {e}")

                    asyncio.create_task(forward_whep_frames())

            # Step 2: Create forwarding track for ComfyStream
            forwarding_track = ForwardingVideoTrack(session.received_frame_queue)

            # Step 3: Connect to ComfyStream
            comfystream_client = ComfyStreamClient(comfystream_url)
            comfystream_pc, data_channel = await comfystream_client.create_connection(
                prompts, width, height, forwarding_track, session.processed_frame_queue
            )

            session.comfystream_peer_connection = comfystream_pc
            session.comfystream_data_channel = data_channel

            # Step 4: Start monitoring processed frames (since we're not publishing)
            async def monitor_processed_frames():
                processed_count = 0
                try:
                    while session.status == 'active':
                        try:
                            if session.processed_frame_queue:
                                frame = await asyncio.wait_for(
                                    session.processed_frame_queue.get(),
                                    timeout=1.0
                                )
                                processed_count += 1
                                if processed_count % 50 == 0:
                                    logger.info(f"Processed {processed_count} frames through ComfyStream")
                        except asyncio.TimeoutError:
                            continue
                except Exception as e:
                    logger.error(f"Error monitoring processed frames: {e}")

            # Start monitoring task
            asyncio.create_task(monitor_processed_frames())

            session.status = 'active'
            logger.info(f"WHEP to ComfyStream relay started: {session_id}")

            return session_id

        except Exception as e:
            logger.error(f"Error starting relay session: {e}")
            session.status = 'error'
            raise

    async def stop_relay(self, session_id: str, bearer_token: Optional[str] = None):
        """Stop a relay session"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]
        session.status = 'stopping'

        try:
            # Close ComfyStream connection
            if session.comfystream_peer_connection:
                await session.comfystream_peer_connection.close()

            # Terminate WHEP session
            if session.whep_resource_url:
                whep_client = WHEPClient()
                await whep_client.terminate_session(session.whep_resource_url, bearer_token)
                await whep_client.close()

            # Close WHEP connection
            if session.whep_peer_connection:
                await session.whep_peer_connection.close()

            # Remove session
            del self.active_sessions[session_id]

            logger.info(f"Relay session {session_id} stopped")

        except Exception as e:
            logger.error(f"Error stopping relay session {session_id}: {e}")

    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a relay session"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]

        return {
            "session_id": session.session_id,
            "status": session.status,
            "whep_endpoint_url": session.whep_endpoint_url,
            "whep_resource_url": session.whep_resource_url,
            "comfystream_url": session.comfystream_url,
            "created_at": session.created_at.isoformat(),
            "queue_sizes": {
                "received_frames": session.received_frame_queue.qsize() if session.received_frame_queue else 0,
                "processed_frames": session.processed_frame_queue.qsize() if session.processed_frame_queue else 0,
            },
            "whep_state": session.whep_peer_connection.connectionState if session.whep_peer_connection else "unknown",
            "comfystream_state": session.comfystream_peer_connection.connectionState if session.comfystream_peer_connection else "unknown",
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions"""
        return [
            {
                "session_id": session.session_id,
                "status": session.status,
                "whep_endpoint_url": session.whep_endpoint_url,
                "created_at": session.created_at.isoformat(),
            }
            for session in self.active_sessions.values()
        ]

async def main():
    """Main function for command line usage"""
    parser = argparse.ArgumentParser(description="WHEP to ComfyStream Relay")
    parser.add_argument("--whep-endpoint", required=True, help="WHEP endpoint URL to subscribe to")
    parser.add_argument("--comfystream-url", default="http://localhost:8889", help="ComfyStream URL (default: http://localhost:8889)")
    parser.add_argument("--bearer-token", help="Bearer token for WHEP authentication")
    parser.add_argument("--prompt", default="enhance this video stream", help="AI processing prompt")
    parser.add_argument("--width", type=int, default=512, help="Frame width (default: 512)")
    parser.add_argument("--height", type=int, default=512, help="Frame height (default: 512)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    relay = WHEPToComfyStreamRelay()

    try:
        logger.info("Starting WHEP to ComfyStream relay...")

        # Start relay session
        session_id = await relay.start_relay(
            whep_endpoint_url=args.whep_endpoint,
            comfystream_url=args.comfystream_url,
            prompts=[{"text": args.prompt}],
            bearer_token=args.bearer_token,
            width=args.width,
            height=args.height
        )

        logger.info(f"Relay session started with ID: {session_id}")
        logger.info("Press Ctrl+C to stop...")

        # Keep running and periodically log status
        while True:
            await asyncio.sleep(10)
            try:
                status = relay.get_session_status(session_id)
                logger.info(f"Session status: {status['status']}, "
                           f"WHEP: {status['whep_state']}, "
                           f"ComfyStream: {status['comfystream_state']}, "
                           f"Queues: {status['queue_sizes']}")
            except Exception as e:
                logger.error(f"Error getting session status: {e}")
                break

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        # Stop all sessions
        for session_id in list(relay.active_sessions.keys()):
            try:
                await relay.stop_relay(session_id, args.bearer_token)
            except Exception as e:
                logger.error(f"Error stopping session {session_id}: {e}")

        logger.info("WHEP to ComfyStream relay stopped")

if __name__ == "__main__":
    asyncio.run(main())
