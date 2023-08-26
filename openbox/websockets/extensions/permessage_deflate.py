from __future__ import annotations

import dataclasses
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .. import exceptions, frames
from ..typing import ExtensionName, ExtensionParameter
from .base import ClientExtensionFactory, Extension, ServerExtensionFactory


__all__ = [
    "PerMessageDeflate",
    "ClientPerMessageDeflateFactory",
    "enable_client_permessage_deflate",
    "ServerPerMessageDeflateFactory",
    "enable_server_permessage_deflate",
]

_EMPTY_UNCOMPRESSED_BLOCK = b"\x00\x00\xff\xff"

_MAX_WINDOW_BITS_VALUES = [str(bits) for bits in range(8, 16)]


class PerMessageDeflate(Extension):
    """Per-Message Deflate extension."""

    name = ExtensionName("permessage-deflate")

    def __init__(
        self,
        remote_no_context_takeover: bool,
        local_no_context_takeover: bool,
        remote_max_window_bits: int,
        local_max_window_bits: int,
        compress_settings: Optional[Dict[Any, Any]] = None,
    ) -> None:
        """Configure the Per-Message Deflate extension."""
        if compress_settings is None:
            compress_settings = {}

        assert remote_no_context_takeover in [False, True]
        assert local_no_context_takeover in [False, True]
        assert 8 <= remote_max_window_bits <= 15
        assert 8 <= local_max_window_bits <= 15
        assert "wbits" not in compress_settings

        self.remote_no_context_takeover = remote_no_context_takeover
        self.local_no_context_takeover = local_no_context_takeover
        self.remote_max_window_bits = remote_max_window_bits
        self.local_max_window_bits = local_max_window_bits
        self.compress_settings = compress_settings

        if not self.remote_no_context_takeover:
            self.decoder = zlib.decompressobj(wbits=-self.remote_max_window_bits)

        if not self.local_no_context_takeover:
            self.encoder = zlib.compressobj(
                wbits=-self.local_max_window_bits, **self.compress_settings
            )

        # To handle continuation frames properly, we must keep track of
        # whether that initial frame was encoded.
        self.decode_cont_data = False
        # There's no need for self.encode_cont_data because we always encode
        # outgoing frames, so it would always be True.

    def __repr__(self) -> str:
        return (
            f"PerMessageDeflate("
            f"remote_no_context_takeover={self.remote_no_context_takeover}, "
            f"local_no_context_takeover={self.local_no_context_takeover}, "
            f"remote_max_window_bits={self.remote_max_window_bits}, "
            f"local_max_window_bits={self.local_max_window_bits})"
        )

    def decode(
        self,
        frame: frames.Frame,
        *,
        max_size: Optional[int] = None,
    ) -> frames.Frame:
        """Decode an incoming frame."""
        # Skip control frames.
        if frame.opcode in frames.CTRL_OPCODES:
            return frame

        # Handle continuation data frames:
        # - skip if the message isn't encoded
        # - reset "decode continuation data" flag if it's a final frame
        if frame.opcode is frames.OP_CONT:
            if not self.decode_cont_data:
                return frame
            if frame.fin:
                self.decode_cont_data = False

        # Handle text and binary data frames:
        # - skip if the message isn't encoded
        # - unset the rsv1 flag on the first frame of a compressed message
        # - set "decode continuation data" flag if it's a non-final frame
        else:
            if not frame.rsv1:
                return frame
            frame = dataclasses.replace(frame, rsv1=False)
            if not frame.fin:
                self.decode_cont_data = True

            # Re-initialize per-message decoder.
            if self.remote_no_context_takeover:
                self.decoder = zlib.decompressobj(wbits=-self.remote_max_window_bits)

        # Uncompress data. Protect against zip bombs by preventing zlib from
        # decompressing more than max_length bytes (except when the limit is
        # disabled with max_size = None).
        data = frame.data
        if frame.fin:
            data += _EMPTY_UNCOMPRESSED_BLOCK
        max_length = 0 if max_size is None else max_size
        try:
            data = self.decoder.decompress(data, max_length)
        except zlib.error as exc:
            raise exceptions.ProtocolError("decompression failed") from exc
        if self.decoder.unconsumed_tail:
            raise exceptions.PayloadTooBig(f"over size limit (? > {max_size} bytes)")

        # Allow garbage collection of the decoder if it won't be reused.
        if frame.fin and self.remote_no_context_takeover:
            del self.decoder

        return dataclasses.replace(frame, data=data)

    def encode(self, frame: frames.Frame) -> frames.Frame:
        """Encode an outgoing frame."""
        # Skip control frames.
        if frame.opcode in frames.CTRL_OPCODES:
            return frame

        # Since we always encode messages, there's no "encode continuation
        # data" flag similar to "decode continuation data" at this time.

        if frame.opcode is not frames.OP_CONT:
            # Set the rsv1 flag on the first frame of a compressed message.
            frame = dataclasses.replace(frame, rsv1=True)
            # Re-initialize per-message decoder.
            if self.local_no_context_takeover:
                self.encoder = zlib.compressobj(
                    wbits=-self.local_max_window_bits, **self.compress_settings
                )

        # Compress data.
        data = self.encoder.compress(frame.data) + self.encoder.flush(zlib.Z_SYNC_FLUSH)
        if frame.fin and data.endswith(_EMPTY_UNCOMPRESSED_BLOCK):
            data = data[:-4]

        # Allow garbage collection of the encoder if it won't be reused.
        if frame.fin and self.local_no_context_takeover:
            del self.encoder

        return dataclasses.replace(frame, data=data)


def _build_parameters(
    server_no_context_takeover: bool,
    client_no_context_takeover: bool,
    server_max_window_bits: Optional[int],
    client_max_window_bits: Optional[Union[int, bool]],
) -> List[ExtensionParameter]:
    """Build a list of ``(name, value)`` pairs for some compression
    parameters."""
    params: List[ExtensionParameter] = []
    if server_no_context_takeover:
        params.append(("server_no_context_takeover", None))
    if client_no_context_takeover:
        params.append(("client_no_context_takeover", None))
    if server_max_window_bits:
        params.append(("server_max_window_bits", str(server_max_window_bits)))
    if client_max_window_bits is True:  # only in handshake requests
        params.append(("client_max_window_bits", None))
    elif client_max_window_bits:
        params.append(("client_max_window_bits", str(client_max_window_bits)))
    return params


def _extract_parameters(
    params: Sequence[ExtensionParameter], *, is_server: bool
) -> Tuple[bool, bool, Optional[int], Optional[Union[int, bool]]]:
    """Extract compression parameters from a list of ``(name, value)`` pairs.

    If ``is_server`` is :obj:`True`, ``client_max_window_bits`` may be provided
    without a value. This is only allowed in handshake requests.
    """
    server_no_context_takeover: bool = False
    client_no_context_takeover: bool = False
    server_max_window_bits: Optional[int] = None
    client_max_window_bits: Optional[Union[int, bool]] = None

    for name, value in params:
        if name == "server_no_context_takeover":
            if server_no_context_takeover:
                raise exceptions.DuplicateParameter(name)
            if value is None:
                server_no_context_takeover = True
            else:
                raise exceptions.InvalidParameterValue(name, value)

        elif name == "client_no_context_takeover":
            if client_no_context_takeover:
                raise exceptions.DuplicateParameter(name)
            if value is None:
                client_no_context_takeover = True
            else:
                raise exceptions.InvalidParameterValue(name, value)

        elif name == "server_max_window_bits":
            if server_max_window_bits is not None:
                raise exceptions.DuplicateParameter(name)
            if value in _MAX_WINDOW_BITS_VALUES:
                server_max_window_bits = int(value)
            else:
                raise exceptions.InvalidParameterValue(name, value)

        elif name == "client_max_window_bits":
            if client_max_window_bits is not None:
                raise exceptions.DuplicateParameter(name)
            if is_server and value is None:  # only in handshake requests
                client_max_window_bits = True
            elif value in _MAX_WINDOW_BITS_VALUES:
                client_max_window_bits = int(value)
            else:
                raise exceptions.InvalidParameterValue(name, value)

        else:
            raise exceptions.InvalidParameterName(name)

    return (
        server_no_context_takeover,
        client_no_context_takeover,
        server_max_window_bits,
        client_max_window_bits,
    )


class ClientPerMessageDeflateFactory(ClientExtensionFactory):
    """Client-side extension factory for the Per-Message Deflate extension.

    Parameters behave as described in `section 7.1 of RFC 7692`_.

    .. _section 7.1 of RFC 7692:
    https://www.rfc-editor.org/rfc/rfc7692.html#section-7.1

    Set them to :obj:`True` to include them in the negotiation offer without a
    value or to an integer value to include them with this value.

    Args:
    server_no_context_takeover: prevent server from using context takeover.
    client_no_context_takeover: prevent client from using context takeover.
    server_max_window_bits: maximum size of the server's LZ77 sliding window
    in bits, between 8 and 15.
    client_max_window_bits: maximum size of the client's LZ77 sliding window
    in bits, between 8 and 15, or :obj:`True` to indicate support without
    setting a limit.
    compress_settings: additional keyword arguments for :func:`zlib.compressobj`,
    excluding ``wbits``.
    """

    name = ExtensionName("permessage-deflate")

    def __init__(
        self,
        server_no_context_takeover: bool = False,
        client_no_context_takeover: bool = False,
        server_max_window_bits: Optional[int] = None,
        client_max_window_bits: Optional[Union[int, bool]] = True,
        compress_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Configure the Per-Message Deflate extension factory."""
        if not (server_max_window_bits is None or 8 <= server_max_window_bits <= 15):
            raise ValueError("server_max_window_bits must be between 8 and 15")
        if not (
            client_max_window_bits is None
            or client_max_window_bits is True
            or 8 <= client_max_window_bits <= 15
        ):
            raise ValueError("client_max_window_bits must be between 8 and 15")
        if compress_settings is not None and "wbits" in compress_settings:
            raise ValueError(
                "compress_settings must not include wbits, "
                "set client_max_window_bits instead"
            )

        self.server_no_context_takeover = server_no_context_takeover
        self.client_no_context_takeover = client_no_context_takeover
        self.server_max_window_bits = server_max_window_bits
        self.client_max_window_bits = client_max_window_bits
        self.compress_settings = compress_settings

    def get_request_params(self) -> List[ExtensionParameter]:
        """Build request parameters."""
        return _build_parameters(
            self.server_no_context_takeover,
            self.client_no_context_takeover,
            self.server_max_window_bits,
            self.client_max_window_bits,
        )

    def process_response_params(
        self,
        params: Sequence[ExtensionParameter],
        accepted_extensions: Sequence[Extension],
    ) -> PerMessageDeflate:
        """Process response parameters.

        Return an extension instance.
        """
        if any(other.name == self.name for other in accepted_extensions):
            raise exceptions.NegotiationError(f"received duplicate {self.name}")

        # Request parameters are available in instance variables.

        # Load response parameters in local variables.
        (
            server_no_context_takeover,
            client_no_context_takeover,
            server_max_window_bits,
            client_max_window_bits,
        ) = _extract_parameters(params, is_server=False)

        # After comparing the request and the response, the final
        # configuration must be available in the local variables.

        # server_no_context_takeover
        #
        #   Req.    Resp.   Result
        #   ------  ------  --------------------------------------------------
        #   False   False   False
        #   False   True    True
        #   True    False   Error!
        #   True    True    True

        if self.server_no_context_takeover:
            if not server_no_context_takeover:
                raise exceptions.NegotiationError("expected server_no_context_takeover")

        # client_no_context_takeover
        #
        #   Req.    Resp.   Result
        #   ------  ------  --------------------------------------------------
        #   False   False   False
        #   False   True    True
        #   True    False   True - must change value
        #   True    True    True

        if self.client_no_context_takeover:
            if not client_no_context_takeover:
                client_no_context_takeover = True

        # server_max_window_bits

        #   Req.    Resp.   Result
        #   ------  ------  --------------------------------------------------
        #   None    None    None
        #   None    8≤M≤15  M
        #   8≤N≤15  None    Error!
        #   8≤N≤15  8≤M≤N   M
        #   8≤N≤15  N<M≤15  Error!

        if self.server_max_window_bits is None:
            pass

        else:
            if server_max_window_bits is None:
                raise exceptions.NegotiationError("expected server_max_window_bits")
            elif server_max_window_bits > self.server_max_window_bits:
                raise exceptions.NegotiationError("unsupported server_max_window_bits")

        # client_max_window_bits

        #   Req.    Resp.   Result
        #   ------  ------  --------------------------------------------------
        #   None    None    None
        #   None    8≤M≤15  Error!
        #   True    None    None
        #   True    8≤M≤15  M
        #   8≤N≤15  None    N - must change value
        #   8≤N≤15  8≤M≤N   M
        #   8≤N≤15  N<M≤15  Error!

        if self.client_max_window_bits is None:
            if client_max_window_bits is not None:
                raise exceptions.NegotiationError("unexpected client_max_window_bits")

        elif self.client_max_window_bits is True:
            pass

        else:
            if client_max_window_bits is None:
                client_max_window_bits = self.client_max_window_bits
            elif client_max_window_bits > self.client_max_window_bits:
                raise exceptions.NegotiationError("unsupported client_max_window_bits")

        return PerMessageDeflate(
            server_no_context_takeover,  # remote_no_context_takeover
            client_no_context_takeover,  # local_no_context_takeover
            server_max_window_bits or 15,  # remote_max_window_bits
            client_max_window_bits or 15,  # local_max_window_bits
            self.compress_settings,
        )


def enable_client_permessage_deflate(
    extensions: Optional[Sequence[ClientExtensionFactory]],
) -> Sequence[ClientExtensionFactory]:
    """Enable Per-Message Deflate with default settings in client extensions.

    If the extension is already present, perhaps with non-default settings, the
    configuration isn't changed.
    """
    if extensions is None:
        extensions = []
    if not any(
        extension_factory.name == ClientPerMessageDeflateFactory.name
        for extension_factory in extensions
    ):
        extensions = list(extensions) + [
            ClientPerMessageDeflateFactory(
                compress_settings={"memLevel": 5},
            )
        ]
    return extensions


class ServerPerMessageDeflateFactory(ServerExtensionFactory):
    """Server-side extension factory for the Per-Message Deflate extension.

    Parameters behave as described in `section 7.1 of RFC 7692`_.

    .. _section 7.1 of RFC 7692:
    https://www.rfc-editor.org/rfc/rfc7692.html#section-7.1

    Set them to :obj:`True` to include them in the negotiation offer without a
    value or to an integer value to include them with this value.

    Args:
    server_no_context_takeover: prevent server from using context takeover.
    client_no_context_takeover: prevent client from using context takeover.
    server_max_window_bits: maximum size of the server's LZ77 sliding window
    in bits, between 8 and 15.
    client_max_window_bits: maximum size of the client's LZ77 sliding window
    in bits, between 8 and 15.
    compress_settings: additional keyword arguments for :func:`zlib.compressobj`,
    excluding ``wbits``.
    require_client_max_window_bits: do not enable compression at all if
    client doesn't advertise support for ``client_max_window_bits``;
    the default behavior is to enable compression without enforcing
    ``client_max_window_bits``.
    """

    name = ExtensionName("permessage-deflate")

    def __init__(
        self,
        server_no_context_takeover: bool = False,
        client_no_context_takeover: bool = False,
        server_max_window_bits: Optional[int] = None,
        client_max_window_bits: Optional[int] = None,
        compress_settings: Optional[Dict[str, Any]] = None,
        require_client_max_window_bits: bool = False,
    ) -> None:
        """Configure the Per-Message Deflate extension factory."""
        if not (server_max_window_bits is None or 8 <= server_max_window_bits <= 15):
            raise ValueError("server_max_window_bits must be between 8 and 15")
        if not (client_max_window_bits is None or 8 <= client_max_window_bits <= 15):
            raise ValueError("client_max_window_bits must be between 8 and 15")
        if compress_settings is not None and "wbits" in compress_settings:
            raise ValueError(
                "compress_settings must not include wbits, "
                "set server_max_window_bits instead"
            )
        if client_max_window_bits is None and require_client_max_window_bits:
            raise ValueError(
                "require_client_max_window_bits is enabled, "
                "but client_max_window_bits isn't configured"
            )

        self.server_no_context_takeover = server_no_context_takeover
        self.client_no_context_takeover = client_no_context_takeover
        self.server_max_window_bits = server_max_window_bits
        self.client_max_window_bits = client_max_window_bits
        self.compress_settings = compress_settings
        self.require_client_max_window_bits = require_client_max_window_bits

    def process_request_params(
        self,
        params: Sequence[ExtensionParameter],
        accepted_extensions: Sequence[Extension],
    ) -> Tuple[List[ExtensionParameter], PerMessageDeflate]:
        """Process request parameters.

        Return response params and an extension instance.
        """
        if any(other.name == self.name for other in accepted_extensions):
            raise exceptions.NegotiationError(f"skipped duplicate {self.name}")

        # Load request parameters in local variables.
        (
            server_no_context_takeover,
            client_no_context_takeover,
            server_max_window_bits,
            client_max_window_bits,
        ) = _extract_parameters(params, is_server=True)

        # Configuration parameters are available in instance variables.

        # After comparing the request and the configuration, the response must
        # be available in the local variables.

        # server_no_context_takeover
        #
        #   Config  Req.    Resp.
        #   ------  ------  --------------------------------------------------
        #   False   False   False
        #   False   True    True
        #   True    False   True - must change value to True
        #   True    True    True

        if self.server_no_context_takeover:
            if not server_no_context_takeover:
                server_no_context_takeover = True

        # client_no_context_takeover
        #
        #   Config  Req.    Resp.
        #   ------  ------  --------------------------------------------------
        #   False   False   False
        #   False   True    True (or False)
        #   True    False   True - must change value to True
        #   True    True    True (or False)

        if self.client_no_context_takeover:
            if not client_no_context_takeover:
                client_no_context_takeover = True

        # server_max_window_bits

        #   Config  Req.    Resp.
        #   ------  ------  --------------------------------------------------
        #   None    None    None
        #   None    8≤M≤15  M
        #   8≤N≤15  None    N - must change value
        #   8≤N≤15  8≤M≤N   M
        #   8≤N≤15  N<M≤15  N - must change value

        if self.server_max_window_bits is None:
            pass

        else:
            if server_max_window_bits is None:
                server_max_window_bits = self.server_max_window_bits
            elif server_max_window_bits > self.server_max_window_bits:
                server_max_window_bits = self.server_max_window_bits

        # client_max_window_bits

        #   Config  Req.    Resp.
        #   ------  ------  --------------------------------------------------
        #   None    None    None
        #   None    True    None - must change value
        #   None    8≤M≤15  M (or None)
        #   8≤N≤15  None    None or Error!
        #   8≤N≤15  True    N - must change value
        #   8≤N≤15  8≤M≤N   M (or None)
        #   8≤N≤15  N<M≤15  N

        if self.client_max_window_bits is None:
            if client_max_window_bits is True:
                client_max_window_bits = self.client_max_window_bits

        else:
            if client_max_window_bits is None:
                if self.require_client_max_window_bits:
                    raise exceptions.NegotiationError("required client_max_window_bits")
            elif client_max_window_bits is True:
                client_max_window_bits = self.client_max_window_bits
            elif self.client_max_window_bits < client_max_window_bits:
                client_max_window_bits = self.client_max_window_bits

        return (
            _build_parameters(
                server_no_context_takeover,
                client_no_context_takeover,
                server_max_window_bits,
                client_max_window_bits,
            ),
            PerMessageDeflate(
                client_no_context_takeover,  # remote_no_context_takeover
                server_no_context_takeover,  # local_no_context_takeover
                client_max_window_bits or 15,  # remote_max_window_bits
                server_max_window_bits or 15,  # local_max_window_bits
                self.compress_settings,
            ),
        )


def enable_server_permessage_deflate(
    extensions: Optional[Sequence[ServerExtensionFactory]],
) -> Sequence[ServerExtensionFactory]:
    """Enable Per-Message Deflate with default settings in server extensions.

    If the extension is already present, perhaps with non-default settings, the
    configuration isn't changed.
    """
    if extensions is None:
        extensions = []
    if not any(
        ext_factory.name == ServerPerMessageDeflateFactory.name
        for ext_factory in extensions
    ):
        extensions = list(extensions) + [
            ServerPerMessageDeflateFactory(
                server_max_window_bits=12,
                client_max_window_bits=12,
                compress_settings={"memLevel": 5},
            )
        ]
    return extensions
