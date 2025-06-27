# Trickle Relay Service

A standalone microservice that acts as a bridge between trickle HTTP segments and WebRTC connections to ComfyStream. This service enables processing of live trickle streams through ComfyStream's AI pipeline.

## Architecture

### Trickle Relay

### Trickle Relay

```
Trickle Input Stream → Decode Frames → WebRTC to ComfyStream → Process Frames → Encode Segments → Trickle Output Stream
```

The Trickle Relay follows this flow:
The Trickle Relay follows this flow:

1. **Trickle Subscriber**: Receives trickle HTTP segments from input stream
2. **Frame Decoder**: Decodes MPEG-TS segments to video frames
1. **Trickle Subscriber**: Receives trickle HTTP segments from input stream
2. **Frame Decoder**: Decodes MPEG-TS segments to video frames
3. **WebRTC Client**: Establishes WebRTC connection to ComfyStream and sends frames
4. **Frame Processor**: Receives processed frames from ComfyStream via WebRTC
5. **Segment Encoder**: Encodes processed frames back to MPEG-TS segments
6. **Trickle Publisher**: Publishes processed segments via trickle HTTP protocol
5. **Segment Encoder**: Encodes processed frames back to MPEG-TS segments
6. **Trickle Publisher**: Publishes processed segments via trickle HTTP protocol

## Features

- **Standalone Operation**: Completely separate from ComfyStream codebase
- **Trickle HTTP Protocol**: Full implementation of trickle streaming protocol
- **WebRTC Integration**: Native WebRTC client for ComfyStream communication
- **Control Message Relay**: Forwards control messages between trickle and WebRTC
- **HTTP API**: RESTful API for session management
- **Real-time Processing**: Low-latency frame processing pipeline
- **Queue Management**: Buffered processing with backpressure handling

## Installation

### Method 1: Docker (Recommended)

The easiest way to run the Trickle Relay Service is using Docker:

```bash
# Build and run with Docker
./docker-build.sh build
./docker-build.sh run

# Or use docker-compose
docker-compose up -d
```

#### Docker Requirements
- Docker Engine 20.10+
- docker-compose 1.29+ (optional)

### Method 2: Python Virtual Environment

#### Dependencies

```bash
pip install aiohttp aiortc av asyncio aiohttp-cors
```

#### Optional Dependencies

- `aiohttp-cors`: For CORS support (recommended)

## Usage

### Trickle Relay Service

#### Starting the Service (Python)
### Trickle Relay Service

#### Starting the Service (Python)

```bash
python trickle_relay_service.py --host 0.0.0.0 --port 8890 --log-level INFO
```

#### Starting the Service (Docker)
#### Starting the Service (Docker)

```bash
./docker-build.sh run
# or
docker-compose up -d
```

#### Command Line Options
#### Command Line Options

- `--host`: Bind host (default: 0.0.0.0)
- `--port`: Bind port (default: 8890)
- `--log-level`: Log level (DEBUG, INFO, WARNING, ERROR)

#### API Endpoints
#### API Endpoints

##### Start Relay Session
##### Start Relay Session

Start a new trickle-to-WebRTC relay session:

```bash
POST /session/start

{
  "input_stream_url": "http://example.com/input/stream",
  "comfystream_url": "http://localhost:8889",
  "prompts": [
    {
      "text": "beautiful landscape, photorealistic"
    }
  ],
  "width": 512,
  "height": 512,
  "output_stream_url": "http://example.com/output/stream"  // optional
}
```

Response:
```json
{
  "success": true,
  "session_id": "uuid-string"
}
```

#### Stop Relay Session

Stop an active relay session:

```bash
DELETE /session/{session_id}
```

Response:
```json
{
  "success": true,
  "message": "Session {session_id} stopped"
}
```

#### Get Session Status

Get detailed status of a relay session:

```bash
GET /session/{session_id}/status
```

Response:
```json
{
  "success": true,
  "session": {
    "session_id": "uuid-string",
    "status": "active",
    "input_stream_url": "http://example.com/input/stream",
    "output_stream_url": "http://example.com/output/stream",
    "comfystream_url": "http://localhost:8889",
    "width": 512,
    "height": 512,
    "created_at": "2024-01-01T12:00:00",
    "queue_sizes": {
      "decoded_frames": 10,
      "processed_frames": 5,
      "output_segments": 2
    },
    "webrtc_state": "connected"
  }
}
```

#### List Sessions

List all active relay sessions:

```bash
GET /sessions
```

Response:
```json
{
  "success": true,
  "sessions": [
    {
      "session_id": "uuid-string",
      "status": "active",
      "input_stream_url": "http://example.com/input/stream",
      "output_stream_url": "http://example.com/output/stream",
      "created_at": "2024-01-01T12:00:00"
    }
  ]
}
```

#### Health Check

Check service health:

```bash
GET /health
```

Response:
```json
{
  "status": "healthy"
  }
  }
```

## Architecture Details

### Trickle Relay Components
### Trickle Relay Components

- **`TrickleSubscriber`**: Handles trickle HTTP protocol for input streams
- **`TrickleStreamDecoder`**: Decodes MPEG-TS segments to video frames
- **`VideoStreamTrack`**: WebRTC track for sending frames to ComfyStream
- **`WebRTCClient`**: Manages WebRTC connection and signaling
- **`TrickleSegmentEncoder`**: Encodes video frames to MPEG-TS segments
- **`TricklePublisher`**: Publishes segments via trickle HTTP protocol

### Processing Pipeline

```
Input Queue (100 frames) → WebRTC → Output Queue (100 frames) → Segment Queue (50 segments)
```

### Error Handling

- **Connection Failures**: Automatic reconnection attempts
- **Frame Processing**: Graceful degradation with error logging
- **Queue Management**: Backpressure handling and monitoring
- **Frame Processing**: Graceful degradation with error logging
- **Queue Management**: Backpressure handling and monitoring
- **WebRTC State**: Connection monitoring and recovery

## Troubleshooting

### Common Issues

1. **WebRTC Connection Failed**
   - Check ComfyStream service is running
   - Verify ComfyStream URL is accessible
   - Ensure prompts are in correct format

2. **Trickle Stream Issues**
   - Verify input stream URL is accessible
   - Check trickle protocol compliance
   - Monitor segment availability

3. **Performance Issues**
   - Monitor queue sizes via status API
   - Adjust frame resolution
   - Check system resources

### Logging

Enable debug logging for detailed information:

```bash
python trickle_relay_service.py --log-level DEBUG
```

### Monitoring

Use the status API to monitor session health:

```bash
watch -n 1 'curl -s http://localhost:8890/session/SESSION_ID/status | jq .session.queue_sizes'
```

## Development

### Code Structure

- `trickle_relay_service.py`: Main service implementation
- `trickle_components.py`: Standalone trickle protocol implementation
- Independent from ComfyStream codebase

### Testing

```python
import asyncio
import aiohttp

async def test_trickle_relay():
async def test_trickle_relay():
    async with aiohttp.ClientSession() as session:
        # Start session
        async with session.post('http://localhost:8890/session/start', json={
            'input_stream_url': 'http://test-input/stream',
            'comfystream_url': 'http://localhost:8889',
            'prompts': [{'text': 'test prompt'}]
        }) as resp:
            result = await resp.json()
            session_id = result['session_id']

        # Check status
        async with session.get(f'http://localhost:8890/session/{session_id}/status') as resp:
            status = await resp.json()
            print(status)

asyncio.run(test_trickle_relay())
asyncio.run(test_trickle_relay())
```

## License

This microservice is designed to work with ComfyStream but operates independently with its own codebase.
