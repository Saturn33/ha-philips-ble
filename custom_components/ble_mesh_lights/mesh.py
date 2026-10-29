"""Bluetooth Mesh over the advertising bearer: unsegmented access messages only.

Not a provisioning client or a general mesh stack: the lights use fixed keys and
IV index, given by the device profile. Encrypts our commands and authenticates
received ones; anything else (control messages, segmented messages, other keys)
is skipped. Based on jphelan82/ha-philips-skylight (MIT).
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.cmac import CMAC

AD_MESH_MESSAGE = 0x2A
DESTINATION_ALL = 0xFFFF
MAX_SEQUENCE = 0xFFFFFF


def _cmac(key: bytes, message: bytes) -> bytes:
    cipher = CMAC(algorithms.AES(key))
    cipher.update(message)
    return cipher.finalize()


def _s1(message: bytes) -> bytes:
    return _cmac(bytes(16), message)


def _k2(key: bytes) -> tuple[int, bytes, bytes]:
    """NID, encryption key and privacy key of a NetKey (managed flooding, P = 0x00)."""
    t = _cmac(_s1(b"smk2"), key)
    t1 = _cmac(t, b"\x00\x01")
    t2 = _cmac(t, t1 + b"\x00\x02")
    t3 = _cmac(t, t2 + b"\x00\x03")
    return t1[-1] & 0x7F, t2, t3


def _k3(key: bytes) -> bytes:
    return _cmac(_cmac(_s1(b"smk3"), key), b"id64\x01")[-8:]


def _k4(key: bytes) -> int:
    return _cmac(_cmac(_s1(b"smk4"), key), b"id6\x01")[-1] & 0x3F


def _aes(key: bytes, block: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(block) + encryptor.finalize()


def ad_structures(data: bytes) -> list[tuple[int, bytes]]:
    """Split advertising data into (AD type, value) pairs; stop at malformed data."""
    result = []
    offset = 0
    while offset < len(data):
        length = data[offset]
        end = offset + length + 1
        if length == 0 or end > len(data):
            break
        result.append((data[offset + 1], data[offset + 2 : end]))
        offset = end
    return result


@dataclass(frozen=True)
class MeshMessage:
    source: int
    destination: int
    sequence: int
    ttl: int
    payload: bytes


class MeshCodec:
    def __init__(self, net_key: bytes, app_key: bytes, iv_index: int = 0) -> None:
        self._app_key = app_key
        self._iv_index = iv_index
        self.nid, self._encryption_key, self._privacy_key = _k2(net_key)
        self.aid = _k4(app_key)
        self.network_id = _k3(net_key)

    def _privacy(self, encrypted: bytes) -> bytes:
        return _aes(self._privacy_key, bytes(5) + self._iv_index.to_bytes(4, "big") + encrypted[:7])[:6]

    def encode(
        self,
        payload: bytes,
        sequence: int,
        source: int,
        destination: int = DESTINATION_ALL,
        ttl: int = 5,
    ) -> bytes:
        """One advertising data structure (length, 0x2A, network PDU)."""
        if not 0 <= sequence <= MAX_SEQUENCE:
            raise ValueError("Mesh sequence out of range; never wrap or reset it")
        if not 1 <= len(payload) <= 11:
            raise ValueError("Only unsegmented access payloads of 1 to 11 bytes are supported")
        seq = sequence.to_bytes(3, "big")
        src = source.to_bytes(2, "big")
        dst = destination.to_bytes(2, "big")
        iv = self._iv_index.to_bytes(4, "big")
        header = bytes([ttl & 0x7F]) + seq + src  # CTL = 0
        upper = AESCCM(self._app_key, tag_length=4).encrypt(b"\x01\x00" + seq + src + dst + iv, payload, None)
        lower = bytes([0x40 | self.aid]) + upper  # SEG = 0, AKF = 1
        encrypted = AESCCM(self._encryption_key, tag_length=4).encrypt(
            b"\x00" + header + b"\x00\x00" + iv, dst + lower, None
        )
        obfuscated = bytes(a ^ b for a, b in zip(header, self._privacy(encrypted), strict=True))
        pdu = bytes([((self._iv_index & 1) << 7) | self.nid]) + obfuscated + encrypted
        return bytes([len(pdu) + 1, AD_MESH_MESSAGE]) + pdu

    def decode(self, advertisement: bytes) -> list[MeshMessage]:
        """Authenticated access messages in an advert's 0x2A structures."""
        messages = []
        for ad_type, pdu in ad_structures(advertisement):
            if ad_type == AD_MESH_MESSAGE:
                message = self._decode_pdu(pdu)
                if message is not None:
                    messages.append(message)
        return messages

    def _decode_pdu(self, pdu: bytes) -> MeshMessage | None:
        if len(pdu) < 18 or pdu[0] != ((self._iv_index & 1) << 7) | self.nid:
            return None
        encrypted = pdu[7:]
        iv = self._iv_index.to_bytes(4, "big")
        header = bytes(a ^ b for a, b in zip(pdu[1:7], self._privacy(encrypted), strict=True))
        if header[0] & 0x80:  # control message
            return None
        try:
            decoded = AESCCM(self._encryption_key, tag_length=4).decrypt(
                b"\x00" + header + b"\x00\x00" + iv, encrypted, None
            )
            if len(decoded) < 8 or decoded[2] != 0x40 | self.aid:  # segmented, device key or other AppKey
                return None
            payload = AESCCM(self._app_key, tag_length=4).decrypt(
                b"\x01\x00" + header[1:6] + decoded[:2] + iv, decoded[3:], None
            )
        except (InvalidTag, ValueError):
            return None
        return MeshMessage(
            source=int.from_bytes(header[4:6], "big"),
            destination=int.from_bytes(decoded[:2], "big"),
            sequence=int.from_bytes(header[1:4], "big"),
            ttl=header[0] & 0x7F,
            payload=payload,
        )
