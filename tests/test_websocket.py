#!/usr/bin/env python3
"""
Test script to verify WebSocket functionality
"""
import asyncio
import websockets
import json
import time

async def test_websocket_connection():
    """Test WebSocket connection to the dashboard"""
    uri = "wss://tvwebhooks.up.railway.app:8081/ws/dashboard?token=0mdUV5n7O534DPPvB_w0fsIYw1XeM7HSBtm_GlS_w8w"
    
    try:
        print("🔌 Attempting to connect to WebSocket server...")
        async with websockets.connect(uri) as websocket:
            print("✅ WebSocket connected successfully!")
            
            # Send subscription message
            await websocket.send(json.dumps({
                "action": "subscribe",
                "payload": {"types": ["all"]}
            }))
            print("📡 Sent subscription message")
            
            # Listen for messages for 10 seconds
            print("👂 Listening for messages...")
            start_time = time.time()
            message_count = 0
            
            while time.time() - start_time < 10:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(message)
                    message_count += 1
                    print(f"📨 Message {message_count}: {data.get('type', 'unknown')} - {data.get('message', 'No message')}")
                    
                    if data.get('type') == 'account_update':
                        print(f"💰 Account balance: ${data.get('data', {}).get('balance', 'N/A')}")
                    elif data.get('type') == 'connected':
                        print(f"🚀 Server time: {data.get('server_time', 'N/A')}")
                        
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"❌ Error receiving message: {e}")
                    break
            
            print(f"📊 Received {message_count} messages in 10 seconds")
            
    except websockets.exceptions.ConnectionClosed as e:
        print(f"❌ WebSocket connection closed: {e}")
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        print("💡 This is expected if WebSocket server is not enabled")
        print("💡 Add WEBSOCKET_ENABLED=true to Railway environment variables")

if __name__ == "__main__":
    print("🧪 WebSocket Connection Test")
    print("=" * 50)
    asyncio.run(test_websocket_connection())
