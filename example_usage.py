#!/usr/bin/env python3
"""
Example Usage of Trickle Relay Service

This script demonstrates how to use the trickle relay service API
to create and manage relay sessions.
"""

import asyncio
import aiohttp
import json
import time

class TrickleRelayClient:
    """Client for interacting with the Trickle Relay Service"""
    
    def __init__(self, base_url: str = "http://localhost:8890"):
        self.base_url = base_url
        
    async def start_session(self, config: dict) -> str:
        """Start a new relay session"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/session/start",
                json=config,
                headers={"Content-Type": "application/json"}
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to start session: {response.status} - {error_text}")
                
                result = await response.json()
                return result['session_id']
    
    async def stop_session(self, session_id: str) -> bool:
        """Stop a relay session"""
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self.base_url}/session/{session_id}"
            ) as response:
                
                return response.status == 200
    
    async def get_session_status(self, session_id: str) -> dict:
        """Get status of a relay session"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/session/{session_id}/status"
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to get status: {response.status} - {error_text}")
                
                result = await response.json()
                return result['session']
    
    async def list_sessions(self) -> list:
        """List all active sessions"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/sessions"
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Failed to list sessions: {response.status} - {error_text}")
                
                result = await response.json()
                return result['sessions']
    
    async def health_check(self) -> bool:
        """Check service health"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/health"
                ) as response:
                    return response.status == 200
        except:
            return False

async def example_basic_usage():
    """Basic usage example"""
    print("🔄 Basic Usage Example")
    print("=" * 50)
    
    client = TrickleRelayClient()
    
    # Check if service is healthy
    print("Checking service health...")
    healthy = await client.health_check()
    if not healthy:
        print("❌ Service is not healthy or not running")
        print("   Make sure to start the service with:")
        print("   python trickle_relay_service.py")
        return
    
    print("✅ Service is healthy")
    
    # Configure relay session
    session_config = {
        "input_stream_url": "http://example.com/input/stream",
        "comfystream_url": "http://localhost:8889",
        "prompts": [
            {
                "text": "beautiful landscape, photorealistic, high quality"
            }
        ],
        "width": 512,
        "height": 512,
        "output_stream_url": "http://example.com/output/stream"
    }
    
    try:
        # Start session
        print("\nStarting relay session...")
        session_id = await client.start_session(session_config)
        print(f"✅ Session started with ID: {session_id}")
        
        # Monitor session for a few seconds
        print("\nMonitoring session status...")
        for i in range(5):
            try:
                status = await client.get_session_status(session_id)
                print(f"Status #{i+1}: {status['status']} - WebRTC: {status['webrtc_state']}")
                print(f"  Queue sizes: {status['queue_sizes']}")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"Error getting status: {e}")
                break
        
        # Stop session
        print(f"\nStopping session {session_id}...")
        success = await client.stop_session(session_id)
        if success:
            print("✅ Session stopped successfully")
        else:
            print("❌ Failed to stop session")
            
    except Exception as e:
        print(f"❌ Error: {e}")

async def example_session_management():
    """Session management example"""
    print("\n🔄 Session Management Example")
    print("=" * 50)
    
    client = TrickleRelayClient()
    
    # Start multiple sessions
    sessions = []
    
    for i in range(3):
        config = {
            "input_stream_url": f"http://example.com/input/stream-{i}",
            "comfystream_url": "http://localhost:8889",
            "prompts": [{"text": f"Test prompt {i}"}],
            "width": 256,
            "height": 256
        }
        
        try:
            session_id = await client.start_session(config)
            sessions.append(session_id)
            print(f"✅ Started session {i+1}: {session_id}")
        except Exception as e:
            print(f"❌ Failed to start session {i+1}: {e}")
    
    # List all sessions
    print(f"\nListing all sessions...")
    try:
        active_sessions = await client.list_sessions()
        print(f"Found {len(active_sessions)} active sessions:")
        for session in active_sessions:
            print(f"  - {session['session_id']}: {session['status']}")
    except Exception as e:
        print(f"❌ Error listing sessions: {e}")
    
    # Stop all sessions
    print("\nStopping all sessions...")
    for session_id in sessions:
        try:
            await client.stop_session(session_id)
            print(f"✅ Stopped session: {session_id}")
        except Exception as e:
            print(f"❌ Failed to stop session {session_id}: {e}")

async def example_error_handling():
    """Error handling example"""
    print("\n🔄 Error Handling Example")
    print("=" * 50)
    
    client = TrickleRelayClient()
    
    # Try invalid configuration
    print("Testing invalid configuration...")
    invalid_config = {
        "input_stream_url": "http://invalid-url",
        # Missing required fields
    }
    
    try:
        session_id = await client.start_session(invalid_config)
        print(f"Unexpected success: {session_id}")
    except Exception as e:
        print(f"✅ Expected error caught: {e}")
    
    # Try accessing non-existent session
    print("\nTesting non-existent session...")
    try:
        status = await client.get_session_status("non-existent-id")
        print(f"Unexpected success: {status}")
    except Exception as e:
        print(f"✅ Expected error caught: {e}")

async def example_comfyui_prompts():
    """ComfyUI prompt format examples"""
    print("\n🔄 ComfyUI Prompt Examples")
    print("=" * 50)
    
    # Simple text prompt
    simple_config = {
        "input_stream_url": "http://example.com/input",
        "comfystream_url": "http://localhost:8889",
        "prompts": [
            {
                "text": "cyberpunk city, neon lights, futuristic"
            }
        ]
    }
    
    # Advanced ComfyUI workflow
    advanced_config = {
        "input_stream_url": "http://example.com/input",
        "comfystream_url": "http://localhost:8889",
        "prompts": [
            {
                "5": {
                    "inputs": {
                        "text": "beautiful landscape, mountains, sunset",
                        "clip": ["23", 0]
                    },
                    "class_type": "CLIPTextEncode",
                    "_meta": {"title": "CLIP Text Encode (Prompt)"}
                },
                "6": {
                    "inputs": {
                        "text": "blurry, low quality, distorted",
                        "clip": ["23", 0]
                    },
                    "class_type": "CLIPTextEncode", 
                    "_meta": {"title": "CLIP Text Encode (Negative)"}
                },
                "16": {
                    "inputs": {
                        "width": 512,
                        "height": 512,
                        "batch_size": 1
                    },
                    "class_type": "EmptyLatentImage",
                    "_meta": {"title": "Empty Latent Image"}
                }
            }
        ]
    }
    
    print("Simple prompt format:")
    print(json.dumps(simple_config["prompts"], indent=2))
    
    print("\nAdvanced ComfyUI workflow format:")
    print(json.dumps(advanced_config["prompts"], indent=2))

async def main():
    """Run all examples"""
    print("🚀 Trickle Relay Service - Usage Examples")
    print("=" * 50)
    print("Make sure the trickle relay service is running:")
    print("  python trickle_relay_service.py --port 8890")
    print("=" * 50)
    
    try:
        # Run examples
        await example_basic_usage()
        await example_session_management() 
        await example_error_handling()
        await example_comfyui_prompts()
        
        print("\n✅ All examples completed!")
        
    except KeyboardInterrupt:
        print("\n⚠️  Examples interrupted by user")
    except Exception as e:
        print(f"\n❌ Error running examples: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 