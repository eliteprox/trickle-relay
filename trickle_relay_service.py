#!/usr/bin/env python3
"""
Trickle Relay Service

A standalone microservice that acts as a bridge between trickle HTTP segments
and WebRTC connections to ComfyStream.

Flow:
1. Subscribe to input trickle stream
2. Decode trickle segments to video frames
3. Send frames to ComfyStream via WebRTC
4. Receive processed frames from ComfyStream
5. Encode processed frames to trickle segments
6. Publish output trickle stream

This service is completely separate from ComfyStream and uses its own codebase.
"""

import asyncio
import aiohttp
import json
import logging
import uuid
import time
import av
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from datetime import datetime

# WebRTC imports
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack
)

logger = logging.getLogger(__name__)

@dataclass
class RelaySession:
    """Represents a trickle-to-WebRTC relay session"""
    session_id: str
    input_stream_url: str
    output_stream_url: str
    comfystream_url: str
    prompts: List[Dict[str, Any]]
    width: int = 512
    height: int = 512
    status: str = 'starting'
    created_at: datetime = None

    # Processing tasks
    subscriber_task: Optional[asyncio.Task] = None
    webrtc_task: Optional[asyncio.Task] = None
    publisher_task: Optional[asyncio.Task] = None

        # Data queues
    decoded_frame_queue: Optional[asyncio.Queue] = None
    processed_frame_queue: Optional[asyncio.Queue] = None
    output_segment_queue: Optional[asyncio.Queue] = None

    # WebRTC components
    peer_connection: Optional[RTCPeerConnection] = None
    data_channel: Optional[Any] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

class VideoStreamTrack(MediaStreamTrack):
    """Video track that sends frames to ComfyStream via WebRTC"""

    kind = "video"

    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True

    async def recv(self):
        """Get next frame to send via WebRTC"""
        try:
            if not self._running:
                raise Exception("Track stopped")

            # Get frame from queue (OPTIMIZED: reduced timeout from 5.0s to 0.1s)
            frame = await asyncio.wait_for(
                self.frame_queue.get(),
                timeout=0.1
            )

            if frame is None:
                self._running = False
                raise Exception("End of stream")

            return frame

        except asyncio.TimeoutError:
            # Generate blank frame to keep connection alive
            blank_frame = av.VideoFrame.from_rgb24(
                bytes([0] * (512 * 512 * 3)),
                width=512,
                height=512
            )
            return blank_frame

    def stop(self):
        self._running = False

class WebRTCClient:
    """WebRTC client for connecting to ComfyStream"""

    def __init__(self, comfystream_url: str):
        self.comfystream_url = comfystream_url
        self.session = aiohttp.ClientSession()

    async def create_connection(self, prompts: List[Dict], width: int, height: int,
                              input_track: VideoStreamTrack,
                              output_queue: asyncio.Queue) -> tuple[RTCPeerConnection, Any]:
        """Create WebRTC connection to ComfyStream"""
        try:
            # Create peer connection
            pc = RTCPeerConnection()

            # Add input track (our decoded frames)
            pc.addTrack(input_track)

            # Create data channel for control messages
            data_channel = pc.createDataChannel("control")

            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track from ComfyStream: {track.kind}")
                if track.kind == "video":
                    # Process incoming frames in background task
                    async def process_incoming_frames():
                        try:
                            while True:
                                frame = await track.recv()
                                await output_queue.put(frame)
                        except Exception as e:
                            logger.error(f"Error processing incoming frames: {e}")

                    asyncio.create_task(process_incoming_frames())

            @data_channel.on("open")
            def on_data_channel_open():
                logger.info("Data channel opened")

                # Send resolution update
                resolution_msg = {
                    "type": "update_resolution",
                    "width": width,
                    "height": height
                }
                data_channel.send(json.dumps(resolution_msg))
                logger.info(f"Sent resolution update: {width}x{height}")

            @data_channel.on("message")
            def on_data_channel_message(message):
                logger.info(f"Received control message: {message}")

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

            logger.info(f"Sending WebRTC offer to {self.comfystream_url}/offer")

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

            logger.info("WebRTC connection established successfully")
            return pc, data_channel

        except Exception as e:
            logger.error(f"Error creating WebRTC connection: {e}")
            raise

    async def close(self):
        """Close the WebRTC client"""
        if self.session:
            await self.session.close()

class TrickleRelayService:
    """Main trickle relay service"""

    def __init__(self, bind_host: str = "0.0.0.0", bind_port: int = 8890):
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.active_sessions: Dict[str, RelaySession] = {}

    async def start_session(self, session_config: Dict[str, Any]) -> str:
        """Start a new relay session"""
        session_id = session_config.get('session_id', str(uuid.uuid4()))

        # Validate required fields
        required_fields = ['input_stream_url', 'comfystream_url', 'prompts']
        for field in required_fields:
            if field not in session_config:
                raise ValueError(f"Missing required field: {field}")

        # Create session
        session = RelaySession(
            session_id=session_id,
            input_stream_url=session_config['input_stream_url'],
            output_stream_url=session_config.get('output_stream_url',
                                               f"{session_config['input_stream_url']}-processed"),
            comfystream_url=session_config['comfystream_url'],
            prompts=session_config['prompts'],
            width=session_config.get('width', 512),
            height=session_config.get('height', 512)
        )

        # Initialize optimized queues for better throughput
        session.decoded_frame_queue = asyncio.Queue(maxsize=1000)   # 1000 frames (was 100)
        session.processed_frame_queue = asyncio.Queue(maxsize=1000) # 1000 frames (was 100)
        session.output_segment_queue = asyncio.Queue(maxsize=200)   # 200 segments (was 50)

        # Store session
        self.active_sessions[session_id] = session

        # Start processing tasks
        session.subscriber_task = asyncio.create_task(
            self._run_trickle_subscriber(session)
        )

        session.webrtc_task = asyncio.create_task(
            self._run_webrtc_processing(session)
        )

        session.publisher_task = asyncio.create_task(
            self._run_trickle_publisher(session)
        )

        session.status = 'active'
        logger.info(f"Started relay session {session_id}")

        return session_id

    async def _run_trickle_subscriber(self, session: RelaySession):
        """Subscribe to input trickle stream and decode frames"""
        from trickle_components import TrickleSubscriber, TrickleStreamDecoder

        try:
            logger.info(f"Starting trickle subscriber for {session.input_stream_url}")

            decoder = TrickleStreamDecoder(
                target_width=session.width,
                target_height=session.height
            )

            async with TrickleSubscriber(session.input_stream_url) as subscriber:
                segment_count = 0
                consecutive_empty = 0
                max_consecutive_empty = 50

                while session.status == 'active':
                    try:
                        # Get next segment
                        segment_reader = await subscriber.next()
                        if segment_reader is None:
                            consecutive_empty += 1
                            if consecutive_empty >= max_consecutive_empty:
                                logger.info("No more segments available")
                                break
                            await asyncio.sleep(0.01)  # OPTIMIZED: 10ms instead of 100ms
                            continue

                        consecutive_empty = 0

                        # Read complete segment data (OPTIMIZED: 64KB chunks instead of 8KB)
                        segment_data = b""
                        while True:
                            chunk = await segment_reader.read(65536)  # 8x larger chunks
                            if not chunk:
                                break
                            segment_data += chunk

                        await segment_reader.close()

                        if segment_data:
                            # Decode segment to frames
                            frames = decoder.process_segment(segment_data)

                            # Queue frames for WebRTC processing
                            for frame in frames:
                                await session.decoded_frame_queue.put(frame)

                            segment_count += 1
                            if segment_count % 10 == 0:
                                logger.info(f"Processed {segment_count} input segments")

                    except Exception as e:
                        logger.error(f"Error in trickle subscriber: {e}")
                        await asyncio.sleep(0.05)  # OPTIMIZED: 50ms instead of 500ms

            logger.info(f"Trickle subscriber finished")

        except Exception as e:
            logger.error(f"Trickle subscriber error: {e}")

    async def _run_webrtc_processing(self, session: RelaySession):
        """Handle WebRTC connection to ComfyStream"""
        try:
            logger.info("Starting WebRTC processing")

            # Create video track for sending frames
            input_track = VideoStreamTrack(session.decoded_frame_queue)

            # Create WebRTC client and connection
            webrtc_client = WebRTCClient(session.comfystream_url)

            try:
                pc, data_channel = await webrtc_client.create_connection(
                    session.prompts,
                    session.width,
                    session.height,
                    input_track,
                    session.processed_frame_queue
                )

                session.peer_connection = pc
                session.data_channel = data_channel

                # Monitor connection state
                @pc.on("connectionstatechange")
                async def on_connection_state_change():
                    logger.info(f"WebRTC state: {pc.connectionState}")
                    if pc.connectionState in ["failed", "closed"]:
                        input_track.stop()

                                # Keep connection alive (OPTIMIZED: faster monitoring)
                while (session.status == 'active' and
                       pc.connectionState not in ["failed", "closed"]):
                    await asyncio.sleep(0.1)  # 100ms instead of 1000ms

            finally:
                await webrtc_client.close()
                input_track.stop()

        except Exception as e:
            logger.error(f"WebRTC processing error: {e}")

    async def _run_trickle_publisher(self, session: RelaySession):
        """Publish processed frames as trickle segments"""
        from trickle_components import TrickleSegmentEncoder, enhanced_segment_publisher

        try:
            logger.info(f"Starting trickle publisher for {session.output_stream_url}")

            encoder = TrickleSegmentEncoder(
                width=session.width,
                height=session.height,
                fps=24,
                format="mpegts",
                video_codec="libx264"
            )

            # Start frame-to-segment conversion task
            async def convert_frames_to_segments():
                frame_batch = []
                batch_size = 24  # 1 second worth of frames at 24fps

                while session.status == 'active':
                    try:
                        # Get processed frame
                        frame = await asyncio.wait_for(
                            session.processed_frame_queue.get(),
                            timeout=1.0
                        )

                        if frame is None:
                            break

                        frame_batch.append(frame)

                        # When batch is full, encode to segment
                        if len(frame_batch) >= batch_size:
                            segment_data = encoder.encode_frames(frame_batch)
                            if segment_data:
                                await session.output_segment_queue.put(segment_data)
                            frame_batch = []

                    except asyncio.TimeoutError:
                        # If we have partial batch, encode it
                        if frame_batch:
                            segment_data = encoder.encode_frames(frame_batch)
                            if segment_data:
                                await session.output_segment_queue.put(segment_data)
                            frame_batch = []
                    except Exception as e:
                        logger.error(f"Error converting frames: {e}")

                # Process remaining frames
                if frame_batch:
                    segment_data = encoder.encode_frames(frame_batch)
                    if segment_data:
                        await session.output_segment_queue.put(segment_data)

                # Signal end
                await session.output_segment_queue.put(None)

            # Start conversion and publishing tasks
            convert_task = asyncio.create_task(convert_frames_to_segments())

            await enhanced_segment_publisher(
                session.output_stream_url,
                session.output_segment_queue,
                target_fps=24.0,
                segment_duration=3.0
            )

            # Wait for conversion to complete
            await convert_task

        except Exception as e:
            logger.error(f"Trickle publisher error: {e}")

    async def stop_session(self, session_id: str):
        """Stop a relay session"""
        try:
            if session_id not in self.active_sessions:
                raise ValueError(f"Session {session_id} not found")

            session = self.active_sessions[session_id]
            session.status = 'stopping'

            logger.info(f"Stopping relay session {session_id}")

            # Cancel tasks
            tasks = [
                session.subscriber_task,
                session.webrtc_task,
                session.publisher_task
            ]

            for task in tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            # Close WebRTC connection
            if session.peer_connection:
                await session.peer_connection.close()

            # Signal end of queues
            for queue in [session.decoded_frame_queue,
                         session.processed_frame_queue,
                         session.output_segment_queue]:
                if queue:
                    try:
                        await queue.put(None)
                    except:
                        pass

            session.status = 'stopped'
            logger.info(f"Stopped relay session {session_id}")

        except Exception as e:
            logger.error(f"Error stopping session {session_id}: {e}")
            raise

    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a session"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]

        return {
            'session_id': session.session_id,
            'status': session.status,
            'input_stream_url': session.input_stream_url,
            'output_stream_url': session.output_stream_url,
            'comfystream_url': session.comfystream_url,
            'width': session.width,
            'height': session.height,
            'created_at': session.created_at.isoformat(),
            'queue_sizes': {
                'decoded_frames': session.decoded_frame_queue.qsize() if session.decoded_frame_queue else 0,
                'processed_frames': session.processed_frame_queue.qsize() if session.processed_frame_queue else 0,
                'output_segments': session.output_segment_queue.qsize() if session.output_segment_queue else 0
            },
            'webrtc_state': session.peer_connection.connectionState if session.peer_connection else 'disconnected'
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions"""
        return [
            {
                'session_id': session.session_id,
                'status': session.status,
                'input_stream_url': session.input_stream_url,
                'output_stream_url': session.output_stream_url,
                'created_at': session.created_at.isoformat()
            }
            for session in self.active_sessions.values()
        ]

# HTTP API handlers
async def start_session_handler(request):
    """HTTP handler to start a relay session"""
    try:
        data = await request.json()
        relay_service = request.app['relay_service']

        session_id = await relay_service.start_session(data)

        return aiohttp.web.json_response({
            'success': True,
            'session_id': session_id
        })

    except Exception as e:
        logger.error(f"Error in start session handler: {e}")
        return aiohttp.web.json_response(
            {'error': str(e)},
            status=500
        )

async def stop_session_handler(request):
    """HTTP handler to stop a relay session"""
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']

        await relay_service.stop_session(session_id)

        return aiohttp.web.json_response({
            'success': True,
            'message': f'Session {session_id} stopped'
        })

    except Exception as e:
        logger.error(f"Error in stop session handler: {e}")
        return aiohttp.web.json_response(
            {'error': str(e)},
            status=500
        )

async def get_session_status_handler(request):
    """HTTP handler to get session status"""
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']

        status = await relay_service.get_session_status(session_id)

        return aiohttp.web.json_response({
            'success': True,
            'session': status
        })

    except Exception as e:
        logger.error(f"Error in session status handler: {e}")
        return aiohttp.web.json_response(
            {'error': str(e)},
            status=500
        )

async def list_sessions_handler(request):
    """HTTP handler to list sessions"""
    try:
        relay_service = request.app['relay_service']
        sessions = relay_service.list_sessions()

        return aiohttp.web.json_response({
            'success': True,
            'sessions': sessions
        })

    except Exception as e:
        logger.error(f"Error in list sessions handler: {e}")
        return aiohttp.web.json_response(
            {'error': str(e)},
            status=500
        )

async def health_handler(request):
    """Health check handler"""
    return aiohttp.web.json_response({'status': 'healthy'})

async def create_app(relay_service: TrickleRelayService):
    """Create aiohttp application"""
    import aiohttp.web as web
    try:
        from aiohttp_cors import setup as setup_cors, ResourceOptions
        cors_available = True
    except ImportError:
        cors_available = False
        logger.warning("aiohttp_cors not available - CORS will not be enabled")

    app = web.Application()
    app['relay_service'] = relay_service

    # Setup CORS if available
    if cors_available:
        cors = setup_cors(app, defaults={
            "*": ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"]
            )
        })

        # Add routes with CORS
        cors.add(app.router.add_post('/session/start', start_session_handler))
        cors.add(app.router.add_delete('/session/{session_id}', stop_session_handler))
        cors.add(app.router.add_get('/session/{session_id}/status', get_session_status_handler))
        cors.add(app.router.add_get('/sessions', list_sessions_handler))
        cors.add(app.router.add_get('/health', health_handler))
    else:
        # Add routes without CORS
        app.router.add_post('/session/start', start_session_handler)
        app.router.add_delete('/session/{session_id}', stop_session_handler)
        app.router.add_get('/session/{session_id}/status', get_session_status_handler)
        app.router.add_get('/sessions', list_sessions_handler)
        app.router.add_get('/health', health_handler)

    return app

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trickle Relay Service")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", default=8890, type=int, help="Bind port")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Log level")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    async def main():
        relay_service = TrickleRelayService(args.host, args.port)
        app = await create_app(relay_service)

        logger.info(f"🚀 Trickle Relay Service starting on {args.host}:{args.port}")
        logger.info("📡 Endpoints available:")
        logger.info(f"  - Start session: POST http://{args.host}:{args.port}/session/start")
        logger.info(f"  - Stop session: DELETE http://{args.host}:{args.port}/session/{{session_id}}")
        logger.info(f"  - Session status: GET http://{args.host}:{args.port}/session/{{session_id}}/status")
        logger.info(f"  - List sessions: GET http://{args.host}:{args.port}/sessions")
        logger.info(f"  - Health check: GET http://{args.host}:{args.port}/health")

        import aiohttp.web as web
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        logger.info("✅ Service started successfully!")
        logger.info("📋 To start a relay session, POST to /session/start with:")
        logger.info("   {")
        logger.info("     \"input_stream_url\": \"http://example.com/input\",")
        logger.info("     \"comfystream_url\": \"http://localhost:8889\",")
        logger.info("     \"prompts\": [{\"text\": \"your prompt here\"}],")
        logger.info("     \"width\": 512,")
        logger.info("     \"height\": 512")
        logger.info("   }")

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            await runner.cleanup()

    asyncio.run(main())
