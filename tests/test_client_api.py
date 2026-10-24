"""Public API + real file inspection/TL encoding with an offline RPC boundary."""
import asyncio
import base64
from fractions import Fraction
import inspect
import math
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import av
import pytest

from fnceitaa import AccountClient, AccountMedia, AccountPeer, AccountProfiles, AccountQueue, AccountUpdates, Client, MediaItem, MediaKind
from fnceitaa.account import AccountHTTPTransport, DEFAULT_ENDPOINT
from fnceitaa.errors import MessageOperationUncertainError
from fnceitaa.session import AccountSession


@pytest.fixture(scope='session')
def files(tmp_path_factory):
    root = tmp_path_factory.mktemp('real-media')
    video = root/'clip.mp4'
    with av.open(str(video), 'w') as output:
        stream = output.add_stream('mpeg4', rate=25)
        stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
        for index in range(7):
            frame = av.VideoFrame(64,48,'yuv420p')
            frame.pts = index
            for plane in frame.planes:
                plane.update(bytes([128])*plane.buffer_size)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    voice = root/'voice.ogg'
    with av.open(str(voice), 'w', format='ogg') as output:
        stream = output.add_stream('libopus', rate=48000)
        stream.layout = 'mono'
        for index in range(10):
            frame = av.AudioFrame(format='flt',layout='mono',samples=960)
            frame.sample_rate, frame.pts, frame.time_base = 48000, index*960, Fraction(1,48000)
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    photo = root/'photo.png'
    photo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    document = root/'report.txt'
    document.write_text('test document',encoding='utf-8')
    return dict(video=video,voice=voice,photo=photo,file=document)


class RPC(AccountHTTPTransport):
    def __init__(self):
        super().__init__(device_id='offline-api-test')
        self.calls=[]
        self.fail_send=False
        self.serialized=[]

    def encode_request(self,method,params=None):
        encoded=super().encode_request(method,params)
        self.serialized.append((method,encoded))
        return encoded

    async def invoke(self,method,params=None):
        self.calls.append((method,params))
        if method in ('messages.getMessages','messages.getHistory'):
            ids=[item['id'] for item in params['id']] if 'id' in params else [10]
            return SimpleNamespace(result=dict(_='messages.messages',users=[],chats=[],messages=[
                dict(_='message',id=identifier,peer_id=dict(_='peerUser',user_id=77),
                     date=1700000000,message='source') for identifier in ids]))
        if method=='messages.forwardMessages':
            return SimpleNamespace(result=dict(_='updates',users=[],chats=[],date=1700000000,
                updates=[dict(_='updateMessageID',random_id=token,id=101+i)
                         for i,token in enumerate(params['random_id'])]))
        if method.startswith('upload.save'):
            return SimpleNamespace(result=True)
        if method=='messages.uploadMedia':
            media=params['media']
            if media['_']=='inputMediaUploadedPhoto':
                result={'_':'messageMediaPhoto','photo':dict(_='photo',id=301,access_hash=42,
                    file_reference=b'offline',dc_id=1,sizes=[dict(_='photoSize',type='x',w=1,h=1,size=20)])}
            else:
                result={'_':'messageMediaDocument','document':dict(_='document',id=302,access_hash=42,
                    file_reference=b'offline',dc_id=1,size=20,mime_type=media['mime_type'],attributes=media['attributes'])}
            return SimpleNamespace(result=result)
        if method in ('messages.sendMessage','messages.sendMedia','messages.sendMultiMedia'):
            if self.fail_send:
                raise ConnectionError('offline simulated loss of response')
            if method=='messages.sendMultiMedia':
                updates=[dict(_='updateMessageID',random_id=item['random_id'],id=101+index)
                         for index,item in enumerate(params['multi_media'])]
                return SimpleNamespace(result=dict(_='updates',updates=updates,users=[],chats=[],date=1700000000))
            return SimpleNamespace(result=dict(_='updateShortSentMessage',id=101,date=1700000000))
        raise AssertionError('Unexpected network operation: '+method)


@pytest.fixture
def account(tmp_path,monkeypatch):
    client=AccountClient(tmp_path/'offline.eitaa',auto_login=False)
    client._session=AccountSession('offline-token','offline-device',77,DEFAULT_ENDPOINT,2496)
    client._connected=client._authorized=True
    rpc=RPC()
    client._transport=rpc
    monkeypatch.setattr(AccountMedia,'_transport',lambda self,role:rpc)
    return client,rpc


@pytest.mark.parametrize('method,media_arg', [('send_video','video'),('send_photo','photo'),
                                          ('send_voice','voice'),('send_file','file')])
def test_only_three_required_media_arguments(method,media_arg):
    signature=inspect.signature(getattr(AccountClient,method))
    required=[name for name,p in signature.parameters.items() if name!='self'
              and p.default is inspect.Parameter.empty and p.kind not in (p.VAR_KEYWORD,p.VAR_POSITIONAL)]
    assert required==['chat_id',media_arg,'caption']
    signature.bind(None,chat_id='self',**{media_arg:Path('local.file')},caption='test')
    with pytest.raises(TypeError):
        signature.bind(None,chat_id='self',**{media_arg:'local.file'})


@pytest.mark.parametrize('owner',[AccountClient,Client,AccountMedia,AccountProfiles,AccountQueue,AccountUpdates])
def test_public_methods_have_chat_id_not_peer(owner):
    for name,method in inspect.getmembers(owner,inspect.isfunction):
        if not name.startswith('_'):
            params=inspect.signature(method).parameters
            assert not {'peer','to_peer','from_peer'} & params.keys(), (owner,name)


@pytest.mark.parametrize('kind',['video','voice','photo','file'])
@pytest.mark.parametrize('positional',[False,True])
def test_minimal_send_reads_real_file_and_preserves_payload_caption_reply(account,files,kind,positional):
    client,rpc=account
    async def scenario():
        method=getattr(client,'send_'+kind)
        if positional:
            sent=await method('self',str(files[kind]),'کپشن')
        else:
            sent=await method(chat_id='self',**{kind:files[kind]},caption='کپشن',reply_to_msg_id=23)
        assert sent.id==101 and sent.has_media and sent.text=='کپشن'
        payload=next(params for name,params in rpc.calls if name=='messages.sendMedia')
        assert payload['peer']=={'_':'inputPeerSelf'} and payload['message']=='کپشن'
        assert payload.get('reply_to_msg_id')==(None if positional else 23)
        assert b''.join(params['bytes'] for name,params in rpc.calls if name=='upload.saveFilePart')==files[kind].read_bytes()
        assert any(name=='messages.sendMedia' for name,_ in rpc.serialized)
        attributes=payload['media'].get('attributes',[])
        if kind=='video':
            attr=next(a for a in attributes if a['_']=='documentAttributeVideo')
            with av.open(str(files[kind])) as actual:
                stream=actual.streams.video[0]
                expected=math.ceil(float(stream.duration*stream.time_base))
            assert attr==dict(_='documentAttributeVideo',duration=expected,w=64,h=48)
        elif kind=='voice':
            assert any(a['_']=='documentAttributeAudio' and a['voice'] and a['duration']>0 for a in attributes)
    asyncio.run(scenario())


def test_partial_metadata_override_keeps_supplied_values(account,files):
    client,rpc=account
    asyncio.run(client.send_video('self',files['video'],'',duration=8,width=100))
    payload=next(p for n,p in rpc.calls if n=='messages.sendMedia')
    assert payload['media']['attributes'][-1]==dict(_='documentAttributeVideo',duration=8,w=100,h=48)


@pytest.mark.parametrize('case',['missing','corrupt','no_video','caption','width','duration'])
def test_bad_inputs_fail_before_any_upload_or_send(account,files,tmp_path,case):
    client,rpc=account
    video,caption,extra=files['video'],'caption',{}
    if case=='missing':
        video=tmp_path/'missing.mp4'
    elif case=='corrupt':
        video=tmp_path/'invalid.mp4'
        video.write_bytes(b'not a video')
    elif case=='no_video':
        video=files['voice']
    elif case=='caption':
        caption=None
    else:
        extra[case]=-1
    with pytest.raises((ValueError,FileNotFoundError)):
        asyncio.run(client.send_video('self',video,caption,**extra))
    assert not rpc.calls


def test_text_accepts_chat_id_and_keeps_real_peer_wire_field(account):
    client,rpc=account
    sent=asyncio.run(client.send_message(chat_id='self',text='hello',reply_to_msg_id=23))
    assert sent.reply_to_msg_id==23
    method,params=rpc.calls[-1]
    assert method=='messages.sendMessage' and params['peer']==dict(_='inputPeerSelf')
    assert 'chat_id' not in params


def test_uncertain_send_is_not_repeated(account,files):
    client,rpc=account
    rpc.fail_send=True
    with pytest.raises(MessageOperationUncertainError):
        asyncio.run(client.send_video(chat_id='self',video=files['video'],caption=''))
    assert sum(n=='messages.sendMedia' for n,_ in rpc.calls)==1


def test_queue_uses_new_keyword_and_dispatches_same_media(account,files,tmp_path):
    client,rpc=account
    async def scenario():
        with client.queue(tmp_path/'queue.sqlite3') as queue:
            job=await queue.enqueue_file(chat_id='self',file=files['video'],caption='queued',kind=MediaKind.VIDEO)
            result=await queue.dispatch_one(job.id)
            assert result.state=='confirmed' and result.receipt['message_ids']==[101]
    asyncio.run(scenario())
    assert any(n=='messages.sendMedia' and p['message']=='queued' for n,p in rpc.calls)


def test_mixed_album_reads_video_metadata_without_required_dimensions(account,files):
    client,rpc=account
    result=asyncio.run(client.send_album(chat_id='self',items=[
        MediaItem(files['photo'],caption='album'),MediaItem(files['video'],kind=MediaKind.VIDEO,caption='video')]))
    assert [m.id for m in result]==[101,102]
    staged=[p for n,p in rpc.calls if n=='messages.uploadMedia']
    assert staged[1]['media']['attributes'][-1]['w']==64
    final=next(p for n,p in rpc.calls if n=='messages.sendMultiMedia')
    assert [m['message'] for m in final['multi_media']]==['album','video']


def test_uploaded_video_can_reuse_explicit_metadata(account,files):
    client,rpc=account
    async def scenario():
        uploaded=await client.upload_file(file=files['video'],chat_id='self')
        before=len(rpc.calls)
        await client.send_video('self',uploaded,'uploaded',duration=1,width=64,height=48)
        assert not any(n.startswith('upload.save') for n,_ in rpc.calls[before:])
    asyncio.run(scenario())


def test_chat_id_read_history_and_forward_keywords_keep_wire_names(account):
    client,rpc=account
    async def scenario():
        message=await client.get_message(chat_id='self',message_id=10)
        history=await client.get_history(chat_id='self',limit=1)
        assert message.id==history[0].id==10
        receipt=await client.forward_message(chat_id=AccountPeer(88,access_hash=22),
            from_chat_id='self',message_id=10)
        assert receipt is not None
    asyncio.run(scenario())
    payload=next(p for n,p in rpc.calls if n=='messages.forwardMessages')
    assert payload['from_peer']==dict(_='inputPeerSelf')
    assert payload['to_peer']['user_id']==88
    assert 'chat_id' not in payload and 'from_chat_id' not in payload


def test_metadata_inspection_does_not_block_async_event_loop(account,files,monkeypatch):
    from fnceitaa import account_media
    original=account_media.media_metadata
    started=threading.Event()
    ticks=[]
    def slow_inspect(*args):
        started.set()
        time.sleep(0.06)
        return original(*args)
    monkeypatch.setattr(account_media,'media_metadata',slow_inspect)
    async def scenario():
        task=asyncio.create_task(account[0].send_video('self',files['video'],''))
        while not started.is_set():
            await asyncio.sleep(0.001)
        while not task.done():
            await asyncio.sleep(0.005)
            ticks.append(1)
        await task
    asyncio.run(scenario())
    assert len(ticks)>=3


def test_bytes_video_with_filename_is_inspected_without_consuming_upload(account,files):
    client,rpc=account
    data=files['video'].read_bytes()
    asyncio.run(client.send_video(chat_id='self',video=data,caption='',filename='clip.mp4'))
    assert b''.join(p['bytes'] for n,p in rpc.calls if n=='upload.saveFilePart')==data


def test_eitaayar_client_and_message_reply_accept_chat_id(files):
    async def scenario():
        client=Client()
        calls=[]
        def send(**kwargs):
            calls.append(kwargs)
            return dict(ok=True,result=dict(message_id=5+len(calls)))
        client._engine=SimpleNamespace(send_message=send,send_file=send)
        first=await client.send_message(chat_id=77,text='first')
        reply=await first.reply('reply')
        document=await client.send_file(chat_id=77,file=files['file'],caption='document')
        assert reply.reply_to_msg_id==first.id and document.text=='document'
        assert calls[1]['chat_id']==77 and calls[1]['reply_to_message_id']==first.id
    asyncio.run(scenario())


def test_cancellation_during_inspection_closes_file_after_probe_and_never_uploads(account,files,monkeypatch):
    from fnceitaa import account_media
    original=account_media.media_metadata
    started,release=threading.Event(),threading.Event()
    observed=[]
    def paused_probe(source,*args):
        started.set()
        assert release.wait(2)
        observed.append(not source.stream.closed)
        return original(source,*args)
    monkeypatch.setattr(account_media,'media_metadata',paused_probe)
    async def scenario():
        task=asyncio.create_task(account[0].send_video('self',files['video'],''))
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
    asyncio.run(scenario())
    assert observed==[True] and account[1].calls==[]
