import asyncio
import contextlib
import struct

import pytest

from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps, loads
from sdl_core.limits import FRAME_BYTES
from sdl_configurator.transport import Client


@contextlib.asynccontextmanager
async def server(tmp_path, reply):
    path = tmp_path/'rpc.sock'
    tasks = set()
    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            while True:
                size, = struct.unpack('>I', await reader.readexactly(4))
                req = loads(await reader.readexactly(size), FRAME_BYTES)
                if req['method'] == 'system.hello':
                    raw = dumps({'jsonrpc':'2.0','id':req['id'],'result':{'apiMajor':1, 'apiMinor':0}})
                    writer.write(struct.pack('>I',len(raw))+raw)
                else:
                    value = reply(req)
                    if isinstance(value, bytes): writer.write(value)
                    else:
                        raw = dumps(value)
                        writer.write(struct.pack('>I',len(raw))+raw)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception): await writer.wait_closed()
            tasks.discard(asyncio.current_task())
    srv = await asyncio.start_unix_server(handle, path=str(path))
    try: yield path
    finally:
        srv.close(); await srv.wait_closed()
        for task in list(tasks): task.cancel()
        if tasks: await asyncio.gather(*list(tasks), return_exceptions=True)


async def test_protocol_handshake_and_result(tmp_path):
    async with server(tmp_path, lambda r: {'jsonrpc':'2.0','id':r['id'],'result':{'revision':4}}) as path:
        async with Client(path) as client:
            assert client.hello['apiMajor'] == 1
            assert (await client.call('system.snapshot'))['revision'] == 4


@pytest.mark.parametrize('header', [0, FRAME_BYTES + 1])
async def test_oversized_or_zero_response_rejected(tmp_path, header):
    async with server(tmp_path, lambda _: struct.pack('>I',header)) as path:
        async with Client(path) as client:
            with pytest.raises(SdlError, match='frame size'): await client.call('system.snapshot')


async def test_mismatched_id_rejected(tmp_path):
    async with server(tmp_path, lambda r: {'jsonrpc':'2.0','id':'other','result':{}}) as path:
        async with Client(path) as client:
            with pytest.raises(SdlError, match='envelope'): await client.call('system.snapshot')


async def test_rpc_error_preserves_structured_code(tmp_path):
    async with server(tmp_path, lambda r: {'jsonrpc':'2.0','id':r['id'],'error':{
        'message':'revision changed','data':{'code':'REVISION_CONFLICT','details':{'currentRevision':3}}}}) as path:
        async with Client(path) as client:
            with pytest.raises(SdlError) as e: await client.call('configuration.apply')
            assert e.value.code == 'REVISION_CONFLICT'
            assert e.value.details['currentRevision'] == 3


async def test_duplicate_json_keys_rejected(tmp_path):
    def reply(r):
        raw = ('{"jsonrpc":"2.0","id":"'+r['id']+'","result":{},"result":{}}').encode()
        return struct.pack('>I',len(raw))+raw
    async with server(tmp_path, reply) as path:
        async with Client(path) as client:
            with pytest.raises(SdlError) as e: await client.call('system.snapshot')
            assert e.value.code == 'INVALID_JSON'


async def test_unavailable_socket_has_actionable_error(tmp_path):
    with pytest.raises(SdlError) as e:
        async with Client(tmp_path/'missing.sock'): pass
    assert e.value.code == 'CONTROLLER_UNAVAILABLE'


async def test_no_automatic_retry_after_interrupted_request(tmp_path):
    calls=[]
    def reply(r):
        calls.append(r['method'])
        return b''
    async with server(tmp_path, reply) as path:
        async with Client(path, timeout=0.08) as client:
            with pytest.raises(SdlError) as e: await client.call('configuration.apply', {'example': True})
            assert e.value.code == 'CONNECTION_LOST'
    assert calls == ['configuration.apply']
