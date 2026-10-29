"""Research view of the air: what an advert is, and why a Mesh message does not decrypt.

The integration's codec silently skips everything it cannot use. When looking at
an unknown device you want the reason instead: other NetKey, other AppKey,
segmented, control message. Crypto comes from the integration's mesh module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from _package import load

mesh = load("mesh")

AD_NAME = (0x08, 0x09)
AD_UUID16_LIST = (0x02, 0x03)
AD_SERVICE_DATA16 = 0x16
AD_MANUFACTURER = 0xFF
AD_PB_ADV = 0x29
AD_MESH_MESSAGE = 0x2A
AD_MESH_BEACON = 0x2B
UUID_MESH_PROVISIONING = 0x1827
UUID_MESH_PROXY = 0x1828

# Keys worth trying on an unknown device.
CANDIDATE_NETKEYS = {
    "11 x 16 (Valen/Skylight factory)": bytes.fromhex("11" * 16),
    "00 x 16": bytes(16),
    "Mesh spec sample": bytes.fromhex("7dd7364cd842ad18c17c2b820c84c3d6"),
    "01..10": bytes(range(1, 17)),
}


@dataclass
class AdvertInfo:
    name: str | None = None
    uuids16: tuple[int, ...] = ()
    service_data: dict[int, bytes] = field(default_factory=dict)
    manufacturer: dict[int, bytes] = field(default_factory=dict)
    mesh_pdus: tuple[bytes, ...] = ()
    mesh_beacons: tuple[bytes, ...] = ()
    pb_adv: bool = False

    @property
    def mesh_related(self) -> bool:
        uuids = set(self.uuids16) | set(self.service_data)
        return bool(
            self.mesh_pdus
            or self.mesh_beacons
            or self.pb_adv
            or uuids & {UUID_MESH_PROVISIONING, UUID_MESH_PROXY}
            or (self.name and "MESH" in self.name.upper())
        )


def parse_advert(data: bytes) -> AdvertInfo:
    info = AdvertInfo()
    uuids, pdus, beacons = [], [], []
    for ad_type, value in mesh.ad_structures(data):
        if ad_type in AD_NAME:
            info.name = value.decode(errors="replace")
        elif ad_type in AD_UUID16_LIST:
            uuids += [int.from_bytes(value[i : i + 2], "little") for i in range(0, len(value) - 1, 2)]
        elif ad_type == AD_SERVICE_DATA16 and len(value) >= 2:
            info.service_data[int.from_bytes(value[:2], "little")] = value[2:]
        elif ad_type == AD_MANUFACTURER and len(value) >= 2:
            info.manufacturer[int.from_bytes(value[:2], "little")] = value[2:]
        elif ad_type == AD_MESH_MESSAGE:
            pdus.append(value)
        elif ad_type == AD_MESH_BEACON:
            beacons.append(value)
        elif ad_type == AD_PB_ADV:
            info.pb_adv = True
    info.uuids16, info.mesh_pdus, info.mesh_beacons = tuple(uuids), tuple(pdus), tuple(beacons)
    return info


def describe_beacon(beacon: bytes) -> str:
    if beacon[:1] == b"\x00" and len(beacon) >= 19:
        return f"unprovisioned device uuid={beacon[1:17].hex()} oob={beacon[17:19].hex()}"
    if beacon[:1] == b"\x01" and len(beacon) >= 22:
        return f"secure network net_id={beacon[2:10].hex()} iv_index={int.from_bytes(beacon[10:14], 'big')}"
    return f"beacon {beacon.hex()}"


def network_ids_in(info: AdvertInfo) -> list[bytes]:
    ids = [b[2:10] for b in info.mesh_beacons if b[:1] == b"\x01" and len(b) >= 10]
    proxy = info.service_data.get(UUID_MESH_PROXY)
    if proxy and proxy[0] == 0x00 and len(proxy) >= 9:
        ids.append(proxy[1:9])
    return ids


@dataclass(frozen=True)
class Diagnosis:
    """Why a network PDU did or did not decode. `error` is None on success."""

    error: str | None
    nid: int
    source: int | None = None
    destination: int | None = None
    sequence: int | None = None
    ttl: int | None = None
    payload: bytes | None = None


def diagnose(net_key: bytes, app_key: bytes, pdu: bytes, iv_index: int = 0) -> Diagnosis:
    nid, encryption_key, privacy_key = mesh._k2(net_key)
    if len(pdu) < 14:
        return Diagnosis("too short", pdu[0] & 0x7F if pdu else -1)
    if pdu[0] & 0x7F != nid:
        return Diagnosis("NID mismatch (other NetKey)", pdu[0] & 0x7F)
    iv = iv_index.to_bytes(4, "big")
    privacy = mesh._aes(privacy_key, bytes(5) + iv + pdu[7:14])[:6]
    header = bytes(a ^ b for a, b in zip(pdu[1:7], privacy, strict=True))
    ctl = header[0] >> 7
    fields = dict(
        nid=nid,
        source=int.from_bytes(header[4:6], "big"),
        sequence=int.from_bytes(header[1:4], "big"),
        ttl=header[0] & 0x7F,
    )
    try:
        decoded = AESCCM(encryption_key, tag_length=8 if ctl else 4).decrypt(
            b"\x00" + header + b"\x00\x00" + iv, pdu[7:], None
        )
    except InvalidTag:
        return Diagnosis("NetMIC fail (other NetKey or IV index)", nid)
    fields["destination"] = int.from_bytes(decoded[:2], "big")
    if ctl:
        return Diagnosis("control message", **fields)
    seg, akf, aid = decoded[2] >> 7, (decoded[2] >> 6) & 1, decoded[2] & 0x3F
    if seg:
        return Diagnosis("segmented message (not decoded)", **fields)
    if not akf:
        return Diagnosis("device-key message", **fields)
    if aid != mesh._k4(app_key):
        return Diagnosis(f"AID mismatch: {aid:#04x} (other AppKey)", **fields)
    try:
        payload = AESCCM(app_key, tag_length=4).decrypt(
            b"\x01\x00" + header[1:6] + decoded[:2] + iv, decoded[3:], None
        )
    except InvalidTag:
        return Diagnosis("AppMIC fail (other AppKey)", **fields)
    return Diagnosis(None, payload=payload, **fields)


def opcode_summary(payload: bytes) -> str:
    if payload and payload[0] >> 6 == 0b11 and len(payload) >= 3:
        cid = int.from_bytes(payload[1:3], "little")
        return f"vendor op={payload[0]:#04x} cid={cid:#06x} params={payload[3:].hex()}"
    return f"sig/other {payload.hex()}"
