"""Read missing video/voice attributes from an already opened local file."""
import math

from .enums import MediaKind


def media_metadata(source, kind, duration=None, width=None, height=None):
    """Preserve explicit overrides; never substitute invented duration or size.

    PyAV uses the local stream held by the uploader. Restore its position so
    inspection cannot consume the bytes subsequently uploaded to Eitaa.
    """
    missing = duration is None or (kind == MediaKind.VIDEO and (width is None or height is None))
    if not missing:
        return duration, width, height
    stream = getattr(source, 'stream', None)
    if stream is None:
        raise ValueError('UploadedFile needs explicit media metadata; use the original local path for automatic inspection')
    import av

    position = stream.tell()
    try:
        stream.seek(0)
        with av.open(stream, mode='r', options={'protocol_whitelist': 'file,pipe'}) as container:
            streams = container.streams.video if kind == MediaKind.VIDEO else container.streams.audio
            if not streams:
                raise ValueError('The local file does not contain the requested video/audio stream')
            track = streams[0]
            if duration is None:
                if track.duration is not None and track.time_base is not None:
                    seconds = float(track.duration * track.time_base)
                elif container.duration is not None:
                    seconds = container.duration / av.time_base
                else:
                    raise ValueError('Cannot determine media duration; supply duration explicitly')
                if not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError('The local media duration must be positive')
                duration = math.ceil(seconds)
            if kind == MediaKind.VIDEO:
                width = track.codec_context.width if width is None else width
                height = track.codec_context.height if height is None else height
    except av.error.FFmpegError:
        raise ValueError('Cannot inspect the local media file; check its contents or provide metadata explicitly') from None
    finally:
        stream.seek(position)
    return duration, width, height
