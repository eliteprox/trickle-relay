#!/usr/bin/env python3
"""
Simple WHIP Server for Testing

A basic WHIP/WHEP server that can stream a video file and provide
endpoints for testing the WHEP client.

This server:
1. Accepts WHIP publishers (can stream video files)
2. Provides WHEP endpoints for subscribers
3. Bridges between WHIP publishers and WHEP subscribers
"""

import asyncio
import aiohttp
import json
import logging
import uuid
import av
from typing import Dict, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from aiohttp import web

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
class StreamSession:
    """Represents a streaming session"""
    session_id: str
    session_type: str  # 'whip' or 'whep'
    peer_connection: RTCPeerConnection
    resource_url: str
    created_at: datetime = field(default_factory=datetime.now)
    tracks: Dict[str, MediaStreamTrack] = field(default_factory=dict)

class VideoFileTrack(MediaStreamTrack):
    """Video track that streams from a video file"""

    kind = "video"

    def __init__(self, video_file_path: str, loop: bool = True):
        super().__init__()
        self.video_file_path = video_file_path
        self.loop = loop
        self._container = None
        self._stream = None
        self._frame_generator = None
        self._running = True
        self.frame_count = 0

    async def _get_next_frame(self):
        """Get next frame from video file"""
        try:
            if self._container is None:
                self._container = av.open(self.video_file_path)
                self._stream = self._container.streams.video[0]
                self._frame_generator = self._container.decode(self._stream)

            if self._frame_generator is None:
                raise Exception("Frame generator not initialized")
            frame = next(self._frame_generator)
            self.frame_count += 1

            if self.frame_count % 100 == 0:
                logger.debug(f"Streamed {self.frame_count} frames from {self.video_file_path}")

            return frame

        except StopIteration:
            # End of file
            if self.loop:
                # Restart from beginning
                logger.info(f"Restarting video file: {self.video_file_path}")
                if self._container:
                    self._container.close()
                self._container = None
                self._stream = None
                self._frame_generator = None
                self.frame_count = 0
                return await self._get_next_frame()
            else:
                self._running = False
                raise Exception("End of video file")
        except Exception as e:
            logger.error(f"Error reading video file {self.video_file_path}: {e}")
            raise

    async def recv(self):
        """Get next frame"""
        if not self._running:
            raise Exception("Track stopped")

        try:
            frame = await asyncio.get_event_loop().run_in_executor(
                None, lambda: asyncio.run(self._get_next_frame())
            )

            # Control frame rate (roughly 30fps)
            await asyncio.sleep(1/30)

            return frame

        except Exception as e:
            logger.error(f"VideoFileTrack recv error: {e}")
            raise

    def stop(self):
        self._running = False
        if self._container:
            self._container.close()

class ForwardingVideoTrack(MediaStreamTrack):
    """Video track that forwards frames from WHIP publishers to WHEP subscribers"""

    kind = "video"

    def __init__(self, source_track: MediaStreamTrack):
        super().__init__()
        self.source_track = source_track
        self._running = True
        self.frame_queue = asyncio.Queue(maxsize=10)
        self._forward_task = None

    async def start_forwarding(self):
        """Start forwarding frames from source track"""
        self._forward_task = asyncio.create_task(self._forward_frames())

    async def _forward_frames(self):
        """Forward frames from source track to queue"""
        try:
            while self._running:
                frame = await self.source_track.recv()

                # Add to queue, drop old frames if queue is full
                if self.frame_queue.full():
                    try:
                        self.frame_queue.get_nowait()  # Drop oldest frame
                    except asyncio.QueueEmpty:
                        pass

                await self.frame_queue.put(frame)

        except Exception as e:
            logger.error(f"Error forwarding frames: {e}")

    async def recv(self):
        """Get next forwarded frame"""
        if not self._running:
            raise Exception("Track stopped")

        try:
            frame = await asyncio.wait_for(self.frame_queue.get(), timeout=1.0)
            return frame
        except asyncio.TimeoutError:
            # Return a black frame if no frame available
            raise Exception("No frame available")

    def stop(self):
        self._running = False
        if self._forward_task:
            self._forward_task.cancel()

class SimpleWHIPServer:
    """Simple WHIP/WHEP server for testing"""

    def __init__(self, video_file_path: Optional[str] = None):
        self.video_file_path = video_file_path
        self.sessions: Dict[str, StreamSession] = {}
        self.active_streams: Dict[str, MediaStreamTrack] = {}  # Stream ID -> Track

        # Create a default video stream from file if provided
        if video_file_path:
            self.default_stream_id = "default"
            self.active_streams[self.default_stream_id] = VideoFileTrack(video_file_path)

    async def handle_whip_request(self, request):
        """Handle WHIP (publisher) requests"""
        try:
            # Get SDP offer
            sdp_offer = await request.text()

            # Create peer connection
            pc = RTCPeerConnection(RTCConfiguration(
                iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")]
            ))

            # Set remote description (offer)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp_offer, type="offer"))

            # Handle incoming tracks
            @pc.on("track")
            def on_track(track):
                logger.info(f"Received WHIP track: {track.kind}")
                if track.kind == "video":
                    # Store the track for WHEP subscribers
                    stream_id = str(uuid.uuid4())
                    self.active_streams[stream_id] = track
                    logger.info(f"Added stream {stream_id} from WHIP publisher")

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Create session
            session_id = str(uuid.uuid4())
            resource_url = f"/whip/sessions/{session_id}"

            session = StreamSession(
                session_id=session_id,
                session_type="whip",
                peer_connection=pc,
                resource_url=resource_url
            )

            self.sessions[session_id] = session

            logger.info(f"WHIP session created: {session_id}")

            # Return answer with Location header
            return web.Response(
                text=pc.localDescription.sdp,
                status=201,
                headers={
                    "Content-Type": "application/sdp",
                    "Location": resource_url
                }
            )

        except Exception as e:
            logger.error(f"WHIP request error: {e}")
            return web.Response(status=500, text=str(e))

    async def handle_whep_request(self, request):
        """Handle WHEP (subscriber) requests"""
        try:
            # Get stream ID from path (e.g., /whep/stream/default)
            stream_id = request.match_info.get('stream_id', 'default')

            if stream_id not in self.active_streams:
                return web.Response(status=404, text="Stream not found")

            # Get SDP offer from client
            sdp_offer = await request.text()

            # Create peer connection
            pc = RTCPeerConnection(RTCConfiguration(
                iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")]
            ))

            # Set remote description (offer)
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp_offer, type="offer"))

            # Add track from active stream
            source_track = self.active_streams[stream_id]
            forwarding_track = ForwardingVideoTrack(source_track)
            await forwarding_track.start_forwarding()
            pc.addTrack(forwarding_track)

            # Create answer
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # Create session
            session_id = str(uuid.uuid4())
            resource_url = f"/whep/sessions/{session_id}"

            session = StreamSession(
                session_id=session_id,
                session_type="whep",
                peer_connection=pc,
                resource_url=resource_url,
                tracks={"video": forwarding_track}
            )

            self.sessions[session_id] = session

            logger.info(f"WHEP session created: {session_id} for stream {stream_id}")

            # Return answer with Location header
            return web.Response(
                text=pc.localDescription.sdp,
                status=201,
                headers={
                    "Content-Type": "application/sdp",
                    "Location": resource_url
                }
            )

        except Exception as e:
            logger.error(f"WHEP request error: {e}")
            return web.Response(status=500, text=str(e))

    async def handle_session_delete(self, request):
        """Handle session deletion (both WHIP and WHEP)"""
        try:
            session_id = request.match_info.get('session_id')

            if session_id not in self.sessions:
                return web.Response(status=404, text="Session not found")

            session = self.sessions[session_id]

            # Close peer connection
            await session.peer_connection.close()

            # Stop tracks
            for track in session.tracks.values():
                if hasattr(track, 'stop'):
                    track.stop()

            # Remove session
            del self.sessions[session_id]

            logger.info(f"Session {session_id} deleted")

            return web.Response(status=200, text="Session deleted")

        except Exception as e:
            logger.error(f"Session delete error: {e}")
            return web.Response(status=500, text=str(e))

    async def handle_status(self, request):
        """Handle status requests"""
        status = {
            "active_sessions": len(self.sessions),
            "active_streams": list(self.active_streams.keys()),
            "sessions": {
                session_id: {
                    "type": session.session_type,
                    "created_at": session.created_at.isoformat(),
                    "connection_state": session.peer_connection.connectionState
                }
                for session_id, session in self.sessions.items()
            }
        }

        return web.json_response(status)

    def create_app(self):
        """Create the web application"""
        app = web.Application()

        # WHIP endpoints
        app.router.add_post('/whip/endpoint', self.handle_whip_request)
        app.router.add_post('/whip/endpoint/{endpoint_id}', self.handle_whip_request)

        # WHEP endpoints
        app.router.add_post('/whep/stream/{stream_id}', self.handle_whep_request)
        app.router.add_post('/whep/stream/default', self.handle_whep_request)

        # Session management
        app.router.add_delete('/whip/sessions/{session_id}', self.handle_session_delete)
        app.router.add_delete('/whep/sessions/{session_id}', self.handle_session_delete)

        # Status
        app.router.add_get('/status', self.handle_status)
        app.router.add_get('/health', self.handle_status)

        return app

async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="Simple WHIP/WHEP Server")
    parser.add_argument("--video-file", help="Video file to stream (optional)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create server
    server = SimpleWHIPServer(video_file_path=args.video_file)
    app = server.create_app()

    # Start server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    logger.info(f"🚀 Simple WHIP/WHEP Server started on http://{args.host}:{args.port}")

    if args.video_file:
        logger.info(f"📹 Streaming video file: {args.video_file}")
        logger.info(f"📡 WHEP endpoint: http://{args.host}:{args.port}/whep/stream/default")
    else:
        logger.info(f"📺 Ready for WHIP publishers on: http://{args.host}:{args.port}/whip/endpoint")

    logger.info(f"📊 Status endpoint: http://{args.host}:{args.port}/status")
    logger.info("Press Ctrl+C to stop...")

    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
