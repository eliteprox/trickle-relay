#!/usr/bin/env python3
"""
Trickle Relay Service - Performance Optimized Version

High-performance microservice optimized for maximum speed from trickle ingest to WebRTC.

Key Optimizations:
1. Parallel segment processing
2. Larger buffers and queues
3. Fast WebRTC frame delivery
4. Prefetching and async I/O
5. Batch processing
6. Zero-copy operations where possible
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
from concurrent.futures import ThreadPoolExecutor
import weakref

# WebRTC imports
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack
)

logger = logging.getLogger(__name__)

@dataclass
class RelaySession:
    """Optimized relay session with performance enhancements"""
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
    subscriber_tasks: List[asyncio.Task] = None  # Multiple parallel subscribers
    decoder_tasks: List[asyncio.Task] = None     # Multiple parallel decoders
    webrtc_task: Optional[asyncio.Task] = None
    publisher_task: Optional[asyncio.Task] = None

    # High-performance queues (much larger buffers)
    segment_queue: Optional[asyncio.Queue] = None        # Raw segments
    decoded_frame_queue: Optional[asyncio.Queue] = None  # Frames to WebRTC
    processed_frame_queue: Optional[asyncio.Queue] = None # Frames from WebRTC
    output_segment_queue: Optional[asyncio.Queue] = None # Output segments

    # WebRTC components
    peer_connection: Optional[RTCPeerConnection] = None
    data_channel: Optional[Any] = None

    # Performance tracking
    stats: Dict[str, int] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.subscriber_tasks is None:
            self.subscriber_tasks = []
        if self.decoder_tasks is None:
            self.decoder_tasks = []
        if self.stats is None:
            self.stats = {
                'segments_fetched': 0,
                'frames_decoded': 0,
                'frames_sent': 0,
                'frames_received': 0
            }

class HighSpeedVideoTrack(MediaStreamTrack):
    """Ultra-fast video track optimized for minimal latency"""

    kind = "video"

    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True
        self._frame_cache = None  # Cache last frame for immediate delivery

    async def recv(self):
        """High-speed frame delivery with zero timeout"""
        try:
            if not self._running:
                raise Exception("Track stopped")

            # Try to get frame immediately without timeout
            try:
                frame = self.frame_queue.get_nowait()
                if frame is None:
                    self._running = False
                    raise Exception("End of stream")
                self._frame_cache = frame
                return frame
            except asyncio.QueueEmpty:
                # If no frame available, return cached frame or generate one
                if self._frame_cache is not None:
                    return self._frame_cache
                else:
                    # Generate minimal blank frame for initial connection
                    blank_frame = av.VideoFrame.from_rgb24(
                        bytes([0] * (512 * 512 * 3)),
                        width=512,
                        height=512
                    )
                    self._frame_cache = blank_frame
                    return blank_frame

        except Exception as e:
            logger.debug(f"VideoTrack recv error: {e}")
            if self._frame_cache is not None:
                return self._frame_cache
            raise

    def stop(self):
        self._running = False

class OptimizedWebRTCClient:
    """High-performance WebRTC client with optimized connection handling"""

    def __init__(self, comfystream_url: str):
        self.comfystream_url = comfystream_url
        # Use connector with optimized settings
        connector = aiohttp.TCPConnector(
            limit=100,  # More connections
            keepalive_timeout=30,
            enable_cleanup_closed=True,
            use_dns_cache=True,
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30, connect=5)
        )

    async def create_connection(self, prompts: List[Dict], width: int, height: int,
                              input_track: HighSpeedVideoTrack,
                              output_queue: asyncio.Queue) -> tuple[RTCPeerConnection, Any]:
        """Create optimized WebRTC connection"""
        try:
            # Create peer connection with optimized configuration
            pc = RTCPeerConnection()

            # Add input track
            pc.addTrack(input_track)

            # Create data channel
            data_channel = pc.createDataChannel("control")

            # High-speed frame receiver
            @pc.on("track")
            def on_track(track):
                logger.info(f"Received track from ComfyStream: {track.kind}")
                if track.kind == "video":
                    # Ultra-fast frame processing task
                    async def process_incoming_frames():
                        try:
                            while True:
                                frame = await track.recv()
                                # Non-blocking queue put with immediate fallback
                                try:
                                    output_queue.put_nowait(frame)
                                except asyncio.QueueFull:
                                    # Drop oldest frame if queue full (prioritize latest)
                                    try:
                                        output_queue.get_nowait()
                                        output_queue.put_nowait(frame)
                                    except asyncio.QueueEmpty:
                                        output_queue.put_nowait(frame)
                        except Exception as e:
                            logger.error(f"Error processing incoming frames: {e}")

                    # Start with high priority
                    task = asyncio.create_task(process_incoming_frames())
                    task.set_name("fast_frame_receiver")

            @data_channel.on("open")
            def on_data_channel_open():
                logger.info("Data channel opened")
                # Send resolution update immediately
                resolution_msg = {
                    "type": "update_resolution",
                    "width": width,
                    "height": height
                }
                data_channel.send(json.dumps(resolution_msg))

            # Fast WebRTC negotiation
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            offer_data = {
                "prompts": prompts,
                "offer": {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type
                }
            }

            # Fast HTTP request
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

            logger.info("High-speed WebRTC connection established")
            return pc, data_channel

        except Exception as e:
            logger.error(f"Error creating WebRTC connection: {e}")
            raise

    async def close(self):
        if self.session:
            await self.session.close()

class OptimizedTrickleRelayService:
    """High-performance trickle relay service"""

    def __init__(self, bind_host: str = "0.0.0.0", bind_port: int = 8890):
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.active_sessions: Dict[str, RelaySession] = {}

        # Thread pool for CPU-intensive operations
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="trickle_decode")

    async def start_session(self, session_config: Dict[str, Any]) -> str:
        """Start optimized relay session"""
        session_id = session_config.get('session_id', str(uuid.uuid4()))

        # Validate required fields
        required_fields = ['input_stream_url', 'comfystream_url', 'prompts']
        for field in required_fields:
            if field not in session_config:
                raise ValueError(f"Missing required field: {field}")

        # Create session with performance optimizations
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

        # Initialize high-capacity queues for maximum throughput
        session.segment_queue = asyncio.Queue(maxsize=500)        # 500 segments buffered
        session.decoded_frame_queue = asyncio.Queue(maxsize=2000) # 2000 frames buffered
        session.processed_frame_queue = asyncio.Queue(maxsize=2000) # 2000 processed frames
        session.output_segment_queue = asyncio.Queue(maxsize=200) # 200 output segments

        # Store session
        self.active_sessions[session_id] = session

        # Start parallel processing tasks for maximum speed
        num_subscribers = 3  # Multiple parallel segment fetchers
        num_decoders = 4     # Multiple parallel decoders

        # Start multiple trickle subscribers for parallel segment fetching
        for i in range(num_subscribers):
            task = asyncio.create_task(
                self._run_parallel_trickle_subscriber(session, subscriber_id=i)
            )
            task.set_name(f"subscriber_{i}")
            session.subscriber_tasks.append(task)

        # Start multiple decoders for parallel frame processing
        for i in range(num_decoders):
            task = asyncio.create_task(
                self._run_parallel_decoder(session, decoder_id=i)
            )
            task.set_name(f"decoder_{i}")
            session.decoder_tasks.append(task)

        # Start WebRTC processing
        session.webrtc_task = asyncio.create_task(
            self._run_optimized_webrtc_processing(session)
        )
        session.webrtc_task.set_name("webrtc_processor")

        # Start publisher
        session.publisher_task = asyncio.create_task(
            self._run_optimized_trickle_publisher(session)
        )
        session.publisher_task.set_name("trickle_publisher")

        session.status = 'active'
        logger.info(f"Started optimized relay session {session_id} with {num_subscribers} subscribers and {num_decoders} decoders")

        return session_id

    async def _run_parallel_trickle_subscriber(self, session: RelaySession, subscriber_id: int):
        """High-speed parallel trickle subscriber"""
        from trickle_components import TrickleSubscriber

        try:
            logger.info(f"Starting high-speed subscriber #{subscriber_id} for {session.input_stream_url}")

            async with TrickleSubscriber(session.input_stream_url) as subscriber:
                segment_count = 0
                consecutive_empty = 0
                max_consecutive_empty = 20  # Reduced for faster response

                while session.status == 'active':
                    try:
                        # Get next segment
                        segment_reader = await subscriber.next()
                        if segment_reader is None:
                            consecutive_empty += 1
                            if consecutive_empty >= max_consecutive_empty:
                                logger.debug(f"Subscriber #{subscriber_id}: No more segments")
                                break
                            # Minimal delay for faster retry
                            await asyncio.sleep(0.01)  # Reduced from 0.1s
                            continue

                        consecutive_empty = 0

                        # Fast segment reading with larger chunks
                        segment_data = b""
                        chunk_size = 65536  # Increased from 8192 to 64KB chunks
                        while True:
                            chunk = await segment_reader.read(chunk_size)
                            if not chunk:
                                break
                            segment_data += chunk

                        await segment_reader.close()

                        if segment_data:
                            # Non-blocking queue put
                            try:
                                session.segment_queue.put_nowait((segment_count, segment_data))
                                session.stats['segments_fetched'] += 1
                                segment_count += 1
                            except asyncio.QueueFull:
                                # Drop oldest segment if queue full
                                try:
                                    session.segment_queue.get_nowait()
                                    session.segment_queue.put_nowait((segment_count, segment_data))
                                    segment_count += 1
                                except asyncio.QueueEmpty:
                                    pass

                    except Exception as e:
                        logger.error(f"Subscriber #{subscriber_id} error: {e}")
                        await asyncio.sleep(0.05)  # Reduced from 0.5s

            logger.info(f"Subscriber #{subscriber_id} finished: {segment_count} segments")

        except Exception as e:
            logger.error(f"Subscriber #{subscriber_id} error: {e}")

    async def _run_parallel_decoder(self, session: RelaySession, decoder_id: int):
        """High-speed parallel segment decoder"""
        from trickle_components import TrickleStreamDecoder

        try:
            logger.info(f"Starting high-speed decoder #{decoder_id}")

            decoder = TrickleStreamDecoder(
                target_width=session.width,
                target_height=session.height
            )

            frames_decoded = 0

            while session.status == 'active':
                try:
                    # Get segment from queue with minimal timeout
                    try:
                        segment_count, segment_data = await asyncio.wait_for(
                            session.segment_queue.get(),
                            timeout=0.1  # Very short timeout
                        )
                    except asyncio.TimeoutError:
                        continue

                    if segment_data is None:
                        break

                    # Decode segment to frames (CPU intensive - consider thread pool)
                    frames = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        decoder.process_segment,
                        segment_data
                    )

                    # Queue frames for WebRTC with batch processing
                    for frame in frames:
                        try:
                            session.decoded_frame_queue.put_nowait(frame)
                            frames_decoded += 1
                            session.stats['frames_decoded'] += 1
                        except asyncio.QueueFull:
                            # Drop oldest frame to maintain flow
                            try:
                                session.decoded_frame_queue.get_nowait()
                                session.decoded_frame_queue.put_nowait(frame)
                                frames_decoded += 1
                            except asyncio.QueueEmpty:
                                pass

                except Exception as e:
                    logger.error(f"Decoder #{decoder_id} error: {e}")
                    continue

            logger.info(f"Decoder #{decoder_id} finished: {frames_decoded} frames")

        except Exception as e:
            logger.error(f"Decoder #{decoder_id} error: {e}")

    async def _run_optimized_webrtc_processing(self, session: RelaySession):
        """Ultra-fast WebRTC processing"""
        try:
            logger.info("Starting optimized WebRTC processing")

            # Create high-speed video track
            input_track = HighSpeedVideoTrack(session.decoded_frame_queue)

            # Create optimized WebRTC client
            webrtc_client = OptimizedWebRTCClient(session.comfystream_url)

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

                # Fast connection monitoring
                @pc.on("connectionstatechange")
                async def on_connection_state_change():
                    logger.info(f"WebRTC state: {pc.connectionState}")
                    if pc.connectionState in ["failed", "closed"]:
                        input_track.stop()

                # Minimal delay connection keepalive
                while (session.status == 'active' and
                       pc.connectionState not in ["failed", "closed"]):
                    await asyncio.sleep(0.1)  # Reduced from 1.0s for faster response

            finally:
                await webrtc_client.close()
                input_track.stop()

        except Exception as e:
            logger.error(f"Optimized WebRTC processing error: {e}")

    async def _run_optimized_trickle_publisher(self, session: RelaySession):
        """High-speed trickle publisher"""
        from trickle_components import TrickleSegmentEncoder, enhanced_segment_publisher

        try:
            logger.info(f"Starting optimized trickle publisher for {session.output_stream_url}")

            encoder = TrickleSegmentEncoder(
                width=session.width,
                height=session.height,
                fps=60,  # Higher FPS for faster processing
                format="mpegts",
                video_codec="libx264"
            )

            # Fast frame-to-segment conversion
            async def convert_frames_to_segments():
                frame_batch = []
                batch_size = 12  # Smaller batches for lower latency (0.5s at 24fps)

                while session.status == 'active':
                    try:
                        # Get processed frame with minimal timeout
                        try:
                            frame = session.processed_frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            # If we have frames, encode them
                            if frame_batch:
                                segment_data = await asyncio.get_event_loop().run_in_executor(
                                    self.executor,
                                    encoder.encode_frames,
                                    frame_batch.copy()
                                )
                                if segment_data:
                                    try:
                                        session.output_segment_queue.put_nowait(segment_data)
                                    except asyncio.QueueFull:
                                        # Drop oldest segment
                                        try:
                                            session.output_segment_queue.get_nowait()
                                            session.output_segment_queue.put_nowait(segment_data)
                                        except asyncio.QueueEmpty:
                                            pass
                                frame_batch = []
                            await asyncio.sleep(0.01)  # Minimal delay
                            continue

                        if frame is None:
                            break

                        frame_batch.append(frame)
                        session.stats['frames_received'] += 1

                        # Encode when batch is full
                        if len(frame_batch) >= batch_size:
                            segment_data = await asyncio.get_event_loop().run_in_executor(
                                self.executor,
                                encoder.encode_frames,
                                frame_batch.copy()
                            )
                            if segment_data:
                                try:
                                    session.output_segment_queue.put_nowait(segment_data)
                                except asyncio.QueueFull:
                                    # Drop oldest
                                    try:
                                        session.output_segment_queue.get_nowait()
                                        session.output_segment_queue.put_nowait(segment_data)
                                    except asyncio.QueueEmpty:
                                        pass
                            frame_batch = []

                    except Exception as e:
                        logger.error(f"Error converting frames: {e}")

                # Process remaining frames
                if frame_batch:
                    segment_data = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        encoder.encode_frames,
                        frame_batch
                    )
                    if segment_data:
                        try:
                            session.output_segment_queue.put_nowait(segment_data)
                        except asyncio.QueueFull:
                            pass

                # Signal end
                await session.output_segment_queue.put(None)

            # Start fast conversion
            convert_task = asyncio.create_task(convert_frames_to_segments())
            convert_task.set_name("fast_frame_converter")

            # Start fast publisher
            await enhanced_segment_publisher(
                session.output_stream_url,
                session.output_segment_queue,
                target_fps=60.0,  # Higher FPS for faster processing
                segment_duration=1.0  # Shorter segments for lower latency
            )

            await convert_task

        except Exception as e:
            logger.error(f"Optimized trickle publisher error: {e}")

    async def stop_session(self, session_id: str):
        """Stop optimized session"""
        try:
            if session_id not in self.active_sessions:
                raise ValueError(f"Session {session_id} not found")

            session = self.active_sessions[session_id]
            session.status = 'stopping'

            logger.info(f"Stopping optimized relay session {session_id}")

            # Cancel all tasks
            all_tasks = (session.subscriber_tasks + session.decoder_tasks +
                        [session.webrtc_task, session.publisher_task])

            for task in all_tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            # Close WebRTC connection
            if session.peer_connection:
                await session.peer_connection.close()

            session.status = 'stopped'
            logger.info(f"Stopped optimized relay session {session_id}")
            logger.info(f"Session stats: {session.stats}")

        except Exception as e:
            logger.error(f"Error stopping session {session_id}: {e}")
            raise

    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get optimized session status with performance metrics"""
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
                'segments': session.segment_queue.qsize() if session.segment_queue else 0,
                'decoded_frames': session.decoded_frame_queue.qsize() if session.decoded_frame_queue else 0,
                'processed_frames': session.processed_frame_queue.qsize() if session.processed_frame_queue else 0,
                'output_segments': session.output_segment_queue.qsize() if session.output_segment_queue else 0
            },
            'performance_stats': session.stats,
            'parallel_workers': {
                'subscribers': len(session.subscriber_tasks),
                'decoders': len(session.decoder_tasks)
            },
            'webrtc_state': session.peer_connection.connectionState if session.peer_connection else 'disconnected'
        }

# Use the optimized service as the main service
TrickleRelayService = OptimizedTrickleRelayService

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Optimized Trickle Relay Service")
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

    logger.info("🚀 Starting OPTIMIZED Trickle Relay Service")
    logger.info("⚡ Performance optimizations enabled:")
    logger.info("  - Parallel segment fetching (3 workers)")
    logger.info("  - Parallel frame decoding (4 workers)")
    logger.info("  - Zero-timeout WebRTC frame delivery")
    logger.info("  - Large buffer queues (2000 frames)")
    logger.info("  - 64KB read chunks")
    logger.info("  - Thread pool for CPU operations")

    # Import and use the existing HTTP handlers
    from trickle_relay_service import (
        start_session_handler, stop_session_handler,
        get_session_status_handler, list_sessions_handler,
        health_handler, create_app
    )

    async def main():
        relay_service = OptimizedTrickleRelayService(args.host, args.port)
        app = await create_app(relay_service)

        logger.info(f"🚀 Optimized Trickle Relay Service starting on {args.host}:{args.port}")

        import aiohttp.web as web
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        logger.info("✅ Optimized service started successfully!")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down optimized service...")
            await runner.cleanup()
            relay_service.executor.shutdown(wait=True)

    asyncio.run(main())
