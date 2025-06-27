#!/usr/bin/env python3
"""
Optimized Trickle Relay Service - Maximum Speed Performance

High-performance microservice optimized for ultra-fast trickle-to-WebRTC processing.

Key Speed Optimizations:
1. Parallel segment processing (3+ concurrent fetchers)
2. Parallel frame decoding (4+ concurrent decoders)
3. Zero-timeout WebRTC frame delivery
4. Large memory buffers (2000+ frame queues)
5. 64KB read chunks instead of 8KB
6. Thread pool for CPU-intensive operations
7. Non-blocking queue operations with overflow handling
8. Minimal sleep delays (0.01s instead of 0.1s+)
9. Batch processing optimizations
10. Fast HTTP connection pooling
"""

import asyncio
import aiohttp
import json
import logging
import uuid
import time
import av
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field
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
class OptimizedRelaySession:
    """Optimized relay session with performance enhancements"""
    session_id: str
    input_stream_url: str
    output_stream_url: str
    comfystream_url: str
    prompts: List[Dict[str, Any]]
    width: int = 512
    height: int = 512
    status: str = 'starting'
    created_at: datetime = field(default_factory=datetime.now)

    # Processing tasks (multiple parallel workers)
    subscriber_tasks: List[asyncio.Task] = field(default_factory=list)
    decoder_tasks: List[asyncio.Task] = field(default_factory=list)
    webrtc_task: Optional[asyncio.Task] = None
    publisher_task: Optional[asyncio.Task] = None

    # High-performance queues (large buffers for speed)
    segment_queue: Optional[asyncio.Queue] = None        # Raw segments (500 buffer)
    decoded_frame_queue: Optional[asyncio.Queue] = None  # Frames to WebRTC (2000 buffer)
    processed_frame_queue: Optional[asyncio.Queue] = None # Frames from WebRTC (2000 buffer)
    output_segment_queue: Optional[asyncio.Queue] = None # Output segments (200 buffer)

    # WebRTC components
    peer_connection: Optional[RTCPeerConnection] = None
    data_channel: Optional[Any] = None

    # Performance tracking
    stats: Dict[str, int] = field(default_factory=lambda: {
        'segments_fetched': 0,
        'frames_decoded': 0,
        'frames_sent': 0,
        'frames_received': 0,
        'queue_overflows': 0
    })

class UltraFastVideoTrack(MediaStreamTrack):
    """Ultra-fast video track with zero-timeout frame delivery"""

    kind = "video"

    def __init__(self, frame_queue: asyncio.Queue):
        super().__init__()
        self.frame_queue = frame_queue
        self._running = True
        self._frame_cache = None  # Cache for immediate delivery

    async def recv(self):
        """Zero-timeout frame delivery for maximum speed"""
        if not self._running:
            raise Exception("Track stopped")

        # Try immediate frame retrieval (no timeout)
        try:
            frame = self.frame_queue.get_nowait()
            if frame is None:
                self._running = False
                raise Exception("End of stream")
            self._frame_cache = frame
            return frame
        except asyncio.QueueEmpty:
            # Return cached frame for continuous flow
            if self._frame_cache is not None:
                return self._frame_cache
            else:
                # Generate minimal frame only once
                self._frame_cache = av.VideoFrame.from_rgb24(
                    bytes([0] * (512 * 512 * 3)),
                    width=512,
                    height=512
                )
                return self._frame_cache

    def stop(self):
        self._running = False

class SpeedOptimizedWebRTCClient:
    """WebRTC client optimized for maximum connection speed"""

    def __init__(self, comfystream_url: str):
        self.comfystream_url = comfystream_url
        # High-performance connector
        connector = aiohttp.TCPConnector(
            limit=200,  # More concurrent connections
            limit_per_host=50,
            keepalive_timeout=60,
            enable_cleanup_closed=True,
            use_dns_cache=True,
            ttl_dns_cache=300,
            limit_open_ssl_client_session_count=10
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15, connect=3)  # Shorter timeouts
        )

    async def create_connection(self, prompts: List[Dict], width: int, height: int,
                              input_track: UltraFastVideoTrack,
                              output_queue: asyncio.Queue) -> tuple[RTCPeerConnection, Any]:
        """Create WebRTC connection optimized for speed"""
        try:
            pc = RTCPeerConnection()
            pc.addTrack(input_track)
            data_channel = pc.createDataChannel("control")

            # Ultra-fast frame receiver
            @pc.on("track")
            def on_track(track):
                logger.info(f"Track received: {track.kind}")
                if track.kind == "video":
                    async def ultra_fast_frame_processor():
                        try:
                            while True:
                                frame = await track.recv()
                                # Non-blocking with overflow handling
                                try:
                                    output_queue.put_nowait(frame)
                                except asyncio.QueueFull:
                                    # Drop oldest frame to maintain flow
                                    try:
                                        output_queue.get_nowait()
                                        output_queue.put_nowait(frame)
                                    except asyncio.QueueEmpty:
                                        output_queue.put_nowait(frame)
                        except Exception as e:
                            logger.error(f"Frame processing error: {e}")

                    task = asyncio.create_task(ultra_fast_frame_processor())
                    task.set_name("ultra_fast_frames")

            @data_channel.on("open")
            def on_data_channel_open():
                # Immediate resolution update
                data_channel.send(json.dumps({
                    "type": "update_resolution",
                    "width": width,
                    "height": height
                }))

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

            # High-speed HTTP request
            async with self.session.post(
                f"{self.comfystream_url}/offer",
                json=offer_data,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    raise Exception(f"Offer failed: {response.status}")
                answer_data = await response.json()

            await pc.setRemoteDescription(RTCSessionDescription(
                sdp=answer_data["sdp"],
                type=answer_data["type"]
            ))

            logger.info("Ultra-fast WebRTC connection established")
            return pc, data_channel

        except Exception as e:
            logger.error(f"WebRTC connection error: {e}")
            raise

    async def close(self):
        if self.session:
            await self.session.close()

class UltraFastTrickleRelayService:
    """Ultra-high-performance trickle relay service"""

    def __init__(self, bind_host: str = "0.0.0.0", bind_port: int = 8890):
        self.bind_host = bind_host
        self.bind_port = bind_port
        self.active_sessions: Dict[str, OptimizedRelaySession] = {}

        # High-performance thread pool for CPU operations
        self.executor = ThreadPoolExecutor(
            max_workers=12,
            thread_name_prefix="ultra_decode"
        )

    async def start_session(self, session_config: Dict[str, Any]) -> str:
        """Start ultra-fast relay session"""
        session_id = session_config.get('session_id', str(uuid.uuid4()))

        # Validate inputs
        required_fields = ['input_stream_url', 'comfystream_url', 'prompts']
        for field in required_fields:
            if field not in session_config:
                raise ValueError(f"Missing required field: {field}")

        # Create optimized session
        session = OptimizedRelaySession(
            session_id=session_id,
            input_stream_url=session_config['input_stream_url'],
            output_stream_url=session_config.get('output_stream_url',
                                               f"{session_config['input_stream_url']}-processed"),
            comfystream_url=session_config['comfystream_url'],
            prompts=session_config['prompts'],
            width=session_config.get('width', 512),
            height=session_config.get('height', 512)
        )

        # Initialize ultra-large queues for maximum throughput
        session.segment_queue = asyncio.Queue(maxsize=1000)        # 1000 segments
        session.decoded_frame_queue = asyncio.Queue(maxsize=5000)  # 5000 frames
        session.processed_frame_queue = asyncio.Queue(maxsize=5000) # 5000 processed
        session.output_segment_queue = asyncio.Queue(maxsize=500)  # 500 output segments

        self.active_sessions[session_id] = session

        # Start multiple parallel workers for maximum speed
        num_subscribers = 4  # 4 parallel segment fetchers
        num_decoders = 6     # 6 parallel decoders

        # Parallel trickle subscribers
        for i in range(num_subscribers):
            task = asyncio.create_task(
                self._ultra_fast_subscriber(session, subscriber_id=i)
            )
            task.set_name(f"ultra_subscriber_{i}")
            session.subscriber_tasks.append(task)

        # Parallel frame decoders
        for i in range(num_decoders):
            task = asyncio.create_task(
                self._ultra_fast_decoder(session, decoder_id=i)
            )
            task.set_name(f"ultra_decoder_{i}")
            session.decoder_tasks.append(task)

        # WebRTC processing
        session.webrtc_task = asyncio.create_task(
            self._ultra_fast_webrtc_processing(session)
        )
        session.webrtc_task.set_name("ultra_webrtc")

        # Publisher
        session.publisher_task = asyncio.create_task(
            self._ultra_fast_publisher(session)
        )
        session.publisher_task.set_name("ultra_publisher")

        session.status = 'active'
        logger.info(f"🚀 Ultra-fast session {session_id} started: {num_subscribers} fetchers, {num_decoders} decoders")

        return session_id

    async def _ultra_fast_subscriber(self, session: OptimizedRelaySession, subscriber_id: int):
        """Ultra-fast parallel trickle subscriber"""
        from trickle_components import TrickleSubscriber

        try:
            logger.info(f"⚡ Ultra-subscriber #{subscriber_id} starting")

            async with TrickleSubscriber(session.input_stream_url) as subscriber:
                segment_count = 0
                consecutive_empty = 0
                max_empty = 10  # Very low threshold for fast response

                while session.status == 'active':
                    try:
                        segment_reader = await subscriber.next()
                        if segment_reader is None:
                            consecutive_empty += 1
                            if consecutive_empty >= max_empty:
                                break
                            await asyncio.sleep(0.005)  # 5ms delay only
                            continue

                        consecutive_empty = 0

                        # Ultra-fast reading with large chunks
                        segment_data = b""
                        chunk_size = 131072  # 128KB chunks for maximum speed

                        while True:
                            chunk = await segment_reader.read(chunk_size)
                            if not chunk:
                                break
                            segment_data += chunk

                        await segment_reader.close()

                        if segment_data:
                            # Non-blocking queue with overflow handling
                            try:
                                session.segment_queue.put_nowait((segment_count, segment_data))
                                session.stats['segments_fetched'] += 1
                            except asyncio.QueueFull:
                                # Drop oldest segment to maintain flow
                                try:
                                    session.segment_queue.get_nowait()
                                    session.segment_queue.put_nowait((segment_count, segment_data))
                                    session.stats['queue_overflows'] += 1
                                except asyncio.QueueEmpty:
                                    pass
                            segment_count += 1

                    except Exception as e:
                        logger.error(f"Subscriber #{subscriber_id} error: {e}")
                        await asyncio.sleep(0.01)  # Minimal error delay

            logger.info(f"⚡ Subscriber #{subscriber_id} finished: {segment_count} segments")

        except Exception as e:
            logger.error(f"Subscriber #{subscriber_id} critical error: {e}")

    async def _ultra_fast_decoder(self, session: OptimizedRelaySession, decoder_id: int):
        """Ultra-fast parallel segment decoder"""
        from trickle_components import TrickleStreamDecoder

        try:
            logger.info(f"⚡ Ultra-decoder #{decoder_id} starting")

            decoder = TrickleStreamDecoder(
                target_width=session.width,
                target_height=session.height
            )

            frames_decoded = 0

            while session.status == 'active':
                try:
                    # Ultra-fast segment retrieval
                    try:
                        segment_count, segment_data = session.segment_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.001)  # 1ms delay only
                        continue

                    if segment_data is None:
                        break

                    # CPU-intensive decoding in thread pool
                    frames = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        decoder.process_segment,
                        segment_data
                    )

                    # Ultra-fast frame queuing with overflow handling
                    for frame in frames:
                        try:
                            session.decoded_frame_queue.put_nowait(frame)
                            frames_decoded += 1
                            session.stats['frames_decoded'] += 1
                        except asyncio.QueueFull:
                            # Drop oldest frame for continuous flow
                            try:
                                session.decoded_frame_queue.get_nowait()
                                session.decoded_frame_queue.put_nowait(frame)
                                session.stats['queue_overflows'] += 1
                            except asyncio.QueueEmpty:
                                session.decoded_frame_queue.put_nowait(frame)

                except Exception as e:
                    logger.error(f"Decoder #{decoder_id} error: {e}")
                    continue

            logger.info(f"⚡ Decoder #{decoder_id} finished: {frames_decoded} frames")

        except Exception as e:
            logger.error(f"Decoder #{decoder_id} critical error: {e}")

    async def _ultra_fast_webrtc_processing(self, session: OptimizedRelaySession):
        """Ultra-fast WebRTC processing"""
        try:
            logger.info("⚡ Ultra-fast WebRTC processing starting")

            input_track = UltraFastVideoTrack(session.decoded_frame_queue)
            webrtc_client = SpeedOptimizedWebRTCClient(session.comfystream_url)

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

                @pc.on("connectionstatechange")
                async def on_connection_state_change():
                    logger.info(f"WebRTC state: {pc.connectionState}")
                    if pc.connectionState in ["failed", "closed"]:
                        input_track.stop()

                # Ultra-fast monitoring loop
                while (session.status == 'active' and
                       pc.connectionState not in ["failed", "closed"]):
                    await asyncio.sleep(0.05)  # 50ms monitoring

            finally:
                await webrtc_client.close()
                input_track.stop()

        except Exception as e:
            logger.error(f"Ultra-fast WebRTC error: {e}")

    async def _ultra_fast_publisher(self, session: OptimizedRelaySession):
        """Ultra-fast trickle publisher"""
        from trickle_components import TrickleSegmentEncoder, enhanced_segment_publisher

        try:
            logger.info(f"⚡ Ultra-fast publisher starting")

            encoder = TrickleSegmentEncoder(
                width=session.width,
                height=session.height,
                fps=60,  # High FPS for speed
                format="mpegts",
                video_codec="libx264"
            )

            # Ultra-fast frame conversion
            async def ultra_fast_frame_conversion():
                frame_batch = []
                batch_size = 6  # Small batches for ultra-low latency

                while session.status == 'active':
                    try:
                        # Ultra-fast frame retrieval
                        try:
                            frame = session.processed_frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            # Process partial batch if available
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
                                        try:
                                            session.output_segment_queue.get_nowait()
                                            session.output_segment_queue.put_nowait(segment_data)
                                        except asyncio.QueueEmpty:
                                            pass
                                frame_batch = []
                            await asyncio.sleep(0.005)  # 5ms delay
                            continue

                        if frame is None:
                            break

                        frame_batch.append(frame)
                        session.stats['frames_received'] += 1

                        # Encode when batch ready
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
                                    try:
                                        session.output_segment_queue.get_nowait()
                                        session.output_segment_queue.put_nowait(segment_data)
                                    except asyncio.QueueEmpty:
                                        pass
                            frame_batch = []

                    except Exception as e:
                        logger.error(f"Frame conversion error: {e}")

                # Final batch
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

                await session.output_segment_queue.put(None)

            convert_task = asyncio.create_task(ultra_fast_frame_conversion())
            convert_task.set_name("ultra_converter")

            # Ultra-fast publisher
            await enhanced_segment_publisher(
                session.output_stream_url,
                session.output_segment_queue,
                target_fps=60.0,
                segment_duration=0.5  # Ultra-short segments
            )

            await convert_task

        except Exception as e:
            logger.error(f"Ultra-fast publisher error: {e}")

    async def stop_session(self, session_id: str):
        """Stop ultra-fast session"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]
        session.status = 'stopping'

        logger.info(f"🛑 Stopping ultra-fast session {session_id}")

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

        if session.peer_connection:
            await session.peer_connection.close()

        session.status = 'stopped'
        logger.info(f"✅ Ultra-fast session {session_id} stopped")
        logger.info(f"📊 Final stats: {session.stats}")

    async def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get ultra-fast session status"""
        if session_id not in self.active_sessions:
            raise ValueError(f"Session {session_id} not found")

        session = self.active_sessions[session_id]

        return {
            'session_id': session.session_id,
            'status': session.status,
            'input_stream_url': session.input_stream_url,
            'output_stream_url': session.output_stream_url,
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
            'optimization_config': {
                'subscribers': len(session.subscriber_tasks),
                'decoders': len(session.decoder_tasks),
                'queue_capacity': {
                    'segments': 1000,
                    'frames': 5000,
                    'processed': 5000,
                    'output': 500
                },
                'chunk_size_kb': 128,
                'batch_size': 6,
                'monitoring_interval_ms': 50
            },
            'webrtc_state': session.peer_connection.connectionState if session.peer_connection else 'disconnected'
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all ultra-fast sessions"""
        return [
            {
                'session_id': session.session_id,
                'status': session.status,
                'input_stream_url': session.input_stream_url,
                'output_stream_url': session.output_stream_url,
                'created_at': session.created_at.isoformat(),
                'performance_stats': session.stats
            }
            for session in self.active_sessions.values()
        ]

# HTTP API handlers (reuse from original service)
async def start_session_handler(request):
    try:
        data = await request.json()
        relay_service = request.app['relay_service']
        session_id = await relay_service.start_session(data)
        return aiohttp.web.json_response({
            'success': True,
            'session_id': session_id
        })
    except Exception as e:
        logger.error(f"Start session error: {e}")
        return aiohttp.web.json_response({'error': str(e)}, status=500)

async def stop_session_handler(request):
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']
        await relay_service.stop_session(session_id)
        return aiohttp.web.json_response({
            'success': True,
            'message': f'Session {session_id} stopped'
        })
    except Exception as e:
        logger.error(f"Stop session error: {e}")
        return aiohttp.web.json_response({'error': str(e)}, status=500)

async def get_session_status_handler(request):
    try:
        session_id = request.match_info['session_id']
        relay_service = request.app['relay_service']
        status = await relay_service.get_session_status(session_id)
        return aiohttp.web.json_response(status)
    except Exception as e:
        logger.error(f"Get status error: {e}")
        return aiohttp.web.json_response({'error': str(e)}, status=500)

async def list_sessions_handler(request):
    try:
        relay_service = request.app['relay_service']
        sessions = relay_service.list_sessions()
        return aiohttp.web.json_response({'sessions': sessions})
    except Exception as e:
        logger.error(f"List sessions error: {e}")
        return aiohttp.web.json_response({'error': str(e)}, status=500)

async def health_handler(request):
    return aiohttp.web.json_response({'status': 'healthy', 'service': 'ultra-fast-trickle-relay'})

async def create_app(relay_service: UltraFastTrickleRelayService):
    """Create ultra-fast web application"""
    app = aiohttp.web.Application()
    app['relay_service'] = relay_service

    # Add routes
    app.router.add_post('/session/start', start_session_handler)
    app.router.add_delete('/session/{session_id}', stop_session_handler)
    app.router.add_get('/session/{session_id}/status', get_session_status_handler)
    app.router.add_get('/sessions', list_sessions_handler)
    app.router.add_get('/health', health_handler)

    return app

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ultra-Fast Trickle Relay Service")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", default=8890, type=int, help="Bind port")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    logger.info("🚀 ULTRA-FAST Trickle Relay Service Starting")
    logger.info("⚡ MAXIMUM SPEED OPTIMIZATIONS ENABLED:")
    logger.info("  ⚡ 4 parallel segment fetchers")
    logger.info("  ⚡ 6 parallel frame decoders")
    logger.info("  ⚡ Zero-timeout WebRTC delivery")
    logger.info("  ⚡ 5000-frame buffer queues")
    logger.info("  ⚡ 128KB read chunks")
    logger.info("  ⚡ Thread pool CPU operations")
    logger.info("  ⚡ Non-blocking queue operations")
    logger.info("  ⚡ 5ms retry delays")
    logger.info("  ⚡ Ultra-low latency batching")

    async def main():
        relay_service = UltraFastTrickleRelayService(args.host, args.port)
        app = await create_app(relay_service)

        logger.info(f"🚀 Ultra-Fast Service ready on {args.host}:{args.port}")

        import aiohttp.web as web
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, args.host, args.port)
        await site.start()

        logger.info("✅ ULTRA-FAST SERVICE ACTIVE!")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down ultra-fast service...")
            await runner.cleanup()
            relay_service.executor.shutdown(wait=True)

    asyncio.run(main())
