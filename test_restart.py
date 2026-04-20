import asyncio
import sys
sys.path.insert(0, 'src')
from aipet.gateway.server import Gateway
from aipet.gateway.config import GatewayConfig

async def test_restart():
    # First gateway
    g1 = Gateway(config=GatewayConfig(ai_provider='echo'))
    await g1.init()
    await g1._handle_chat_send('c1', {'session_id': 'main', 'content': 'test message'})
    session = g1.sessions.get('main')
    print(f'Gateway 1 messages: {len(session.messages)}')
    
    # Second gateway (restart)
    g2 = Gateway(config=GatewayConfig(ai_provider='echo'))
    await g2.init()
    session = g2.sessions.get('main')
    print(f'Gateway 2 messages: {len(session.messages)}')
    
    # Get history
    responses = []
    async def mock_send(client_id, data):
        responses.append(data)
    g2._send = mock_send
    await g2._handle_chat_history('c1', {'session_id': 'main', 'limit': 10})
    msgs = responses[0]['payload']['messages']
    print(f'History returned: {len(msgs)}')

asyncio.run(test_restart())
