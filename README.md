# Trickle Relay Service

A standalone microservice that acts as a bridge between trickle HTTP segments and WebRTC connections to ComfyStream. This service enables processing of live trickle streams through ComfyStream's AI pipeline.

## Architecture

```
Trickle Input Stream → Decode Frames → WebRTC to ComfyStream → Process Frames → Encode Segments → Trickle Output Stream
```

The microservice follows the flow described in the diagram:

1. **Trickle Subscriber**: Ingests trickle HTTP segments from input streams
2. **Frame Decoder**: Decodes trickle segments into individual video frames
3. **WebRTC Client**: Establishes WebRTC connection to ComfyStream and sends frames
4. **Frame Processor**: Receives processed frames from ComfyStream via WebRTC
5. **Segment Encoder**: Encodes processed frames back into trickle segments
6. **Trickle Publisher**: Publishes output trickle stream

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

## Docker Usage

### Quick Start with Docker

1. **Build the image:**
```bash
./docker-build.sh build
```

2. **Run the container:**
```bash
./docker-build.sh run
```

3. **Check service health:**
```bash
./docker-build.sh health
```

### Docker Commands

The `docker-build.sh` script provides convenient commands:

```bash
# Build Docker image
./docker-build.sh build

# Run container
./docker-build.sh run

# Stop container
./docker-build.sh stop

# Restart container
./docker-build.sh restart

# View logs
./docker-build.sh logs
./docker-build.sh logs -f  # Follow logs

# Open shell in container
./docker-build.sh shell

# Check health
./docker-build.sh health

# Clean up (remove container and image)
./docker-build.sh clean
```

### Docker Compose

For production deployment, use docker-compose:

```bash
# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Or use the helper script
./docker-build.sh compose-up
./docker-build.sh compose-down
```

### Environment Variables

Configure the service using environment variables:

```bash
# In .env file or docker-compose.yml
RELAY_HOST=0.0.0.0
RELAY_PORT=8890
LOG_LEVEL=INFO
DEFAULT_COMFYSTREAM_URL=http://localhost:8889
FRAME_BATCH_SIZE=24
TARGET_FPS=24.0
```

### Volume Mounts

The Docker setup includes volume mounts for:
- `/app/logs` - Persistent log storage
- `/app/data` - Application data
- `.env` - Configuration file (read-only)

## Usage

### Starting the Service (Python)

```bash
python trickle_relay_service.py --host 0.0.0.0 --port 8890 --log-level INFO
```

### Starting the Service (Docker)

```bash
./docker-build.sh run
# or
docker-compose up -d
```

### Command Line Options

- `--host`: Bind host (default: 0.0.0.0)
- `--port`: Bind port (default: 8890)
- `--log-level`: Log level (DEBUG, INFO, WARNING, ERROR)

### API Endpoints

#### Start Relay Session

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
```

## Configuration

### Session Configuration

- `input_stream_url`: URL of the input trickle stream
- `comfystream_url`: URL of the ComfyStream service
- `prompts`: Array of prompt objects for ComfyUI processing
- `width`: Target frame width (default: 512)
- `height`: Target frame height (default: 512)
- `output_stream_url`: URL for output trickle stream (optional)

### Prompt Format

Prompts should follow ComfyUI workflow format. Simple text prompts are automatically converted:

```json
{
  "prompts": [
    {
      "text": "your prompt here"
    }
  ]
}
```

For advanced workflows, use full ComfyUI format:

```json
{
  "prompts": [
    {
      "5": {
        "inputs": {
          "text": "beautiful landscape",
          "clip": ["23", 0]
        },
        "class_type": "CLIPTextEncode"
      }
    }
  ]
}
```

## Integration Example

### With Trickle Stream (Python)

1. Start ComfyStream service:
```bash
python app.py --mode=trickle --port 8889
```

2. Start Trickle Relay Service:
```bash
python trickle_relay_service.py --port 8890
```

3. Start relay session:
```bash
curl -X POST http://localhost:8890/session/start \
  -H "Content-Type: application/json" \
  -d '{
    "input_stream_url": "http://input-server/stream",
    "comfystream_url": "http://localhost:8889",
    "prompts": [{"text": "enhance this image"}],
    "width": 512,
    "height": 512
  }'
```

### With Docker

1. Start Trickle Relay Service:
```bash
# Using docker-build script
./docker-build.sh build
./docker-build.sh run

# Or using docker-compose
docker-compose up -d
```

2. Start relay session:
```bash
curl -X POST http://localhost:8890/session/start \
  -H "Content-Type: application/json" \
  -d '{
    "input_stream_url": "http://input-server/stream",
    "comfystream_url": "http://host.docker.internal:8889",
    "prompts": [{"text": "enhance this image"}],
    "width": 512,
    "height": 512
  }'
```

### Docker Network Setup

When running both services in Docker:

```yaml
# docker-compose.yml
version: '3.8'
services:
  trickle-relay:
    build: .
    ports:
      - "8890:8890"
    environment:
      - DEFAULT_COMFYSTREAM_URL=http://comfystream:8889
    networks:
      - ai-network

  comfystream:
    image: comfystream:latest
    ports:
      - "8889:8889"
    networks:
      - ai-network
    command: ["python", "app.py", "--mode=trickle", "--port", "8889"]

networks:
  ai-network:
    driver: bridge
```

### Processing Flow

1. **Input**: Service subscribes to `input_stream_url` using trickle protocol
2. **Decode**: Segments are decoded into video frames
3. **WebRTC**: Frames sent to ComfyStream via WebRTC for AI processing
4. **Process**: ComfyStream applies AI effects and sends processed frames back
5. **Encode**: Processed frames are encoded into trickle segments
6. **Output**: Segments published to output trickle stream

## Architecture Details

### Components

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
- **Frame Processing**: Graceful degradation with blank frames
- **Queue Overflow**: Backpressure management
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

Test with a simple relay session:

```python
import asyncio
import aiohttp

async def test_relay():
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

asyncio.run(test_relay())
```

## License

This microservice is designed to work with ComfyStream but operates independently with its own codebase.
