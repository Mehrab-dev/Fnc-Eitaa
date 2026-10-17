"""Offline by default; --live probes only an unauthenticated read-only method."""
import argparse
import asyncio

from fnceitaa.account import AccountHTTPTransport
from fnceitaa.errors import AccountRPCError


async def run(live=False):
    transport = AccountHTTPTransport(device_id='000000000000001__web')
    packet = transport.encode_request('updates.getState')
    print('Empty account token; synthetic device ID; read-only updates.getState')
    print('Request hex:', packet.hex())
    if not live:
        print('Offline mode. Add --live to send this unauthenticated probe.')
        return
    try:
        reply = await transport.invoke('updates.getState')
    except AccountRPCError as exc:
        print(f'Account RPC response: {exc.code} / {exc.rpc_type}')
        if exc.code != 401 or exc.rpc_type != 'INVALID_LOGIN':
            raise
        print('Unauthenticated probe only; experimental login is in examples.account_login.')
    else:
        print(f'Received result for local request {reply.request_id}; no login claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send the empty-token read-only probe')
    args = parser.parse_args()
    asyncio.run(run(args.live))


if __name__ == '__main__':
    main()
