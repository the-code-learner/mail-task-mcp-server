from __future__ import annotations

from dataclasses import dataclass


class JIDError(ValueError):
    pass


_DOMAIN_TYPES = {
    "s.whatsapp.net": 0,
    "c.us": 0,
    "lid": 1,
    "hosted": 128,
    "hosted.lid": 129,
}


@dataclass(frozen=True, slots=True)
class JID:
    user: str
    server: str
    device: int | None = None
    agent: int | None = None
    domain_type: int = 0

    def __post_init__(self) -> None:
        if not self.server or "@" in self.server:
            raise JIDError("JID server is required and must not contain @")
        if self.device is not None and not 0 <= int(self.device) <= 255:
            raise JIDError("JID device must fit one byte")
        if self.agent is not None and int(self.agent) < 0:
            raise JIDError("JID agent must be non-negative")
        if not 0 <= int(self.domain_type) <= 255:
            raise JIDError("JID domain_type must fit one byte")

    @property
    def bare(self) -> str:
        return f"{self.user}@{self.server}"

    @property
    def is_group(self) -> bool:
        return self.server == "g.us"

    @property
    def is_broadcast(self) -> bool:
        return self.server == "broadcast"

    @property
    def is_newsletter(self) -> bool:
        return self.server == "newsletter"

    @property
    def is_lid(self) -> bool:
        return self.server in {"lid", "hosted.lid"}

    def normalized_user(self) -> "JID":
        server = "s.whatsapp.net" if self.server == "c.us" else self.server
        return JID(self.user, server, domain_type=_DOMAIN_TYPES.get(server, self.domain_type))

    def __str__(self) -> str:
        left = self.user
        if self.agent not in (None, 0):
            left += f"_{int(self.agent)}"
        if self.device not in (None, 0):
            left += f":{int(self.device)}"
        return f"{left}@{self.server}"


def parse_jid(value: str) -> JID:
    raw = str(value or "").strip()
    pos = raw.find("@")
    if pos < 0:
        raise JIDError("JID must contain @")
    left, server = raw[:pos], raw[pos + 1 :]
    if not server:
        raise JIDError("JID server is empty")

    device: int | None = None
    agent: int | None = None
    if ":" in left:
        left, device_raw = left.rsplit(":", 1)
        if not device_raw.isdigit():
            raise JIDError("JID device must be numeric")
        device = int(device_raw)
    if "_" in left:
        user, agent_raw = left.rsplit("_", 1)
        if agent_raw.isdigit():
            left = user
            agent = int(agent_raw)

    return JID(
        user=left,
        server=server,
        device=device,
        agent=agent,
        domain_type=_DOMAIN_TYPES.get(server, agent or 0),
    )


def encode_jid(user: str | int | None, server: str, device: int | None = None, agent: int | None = None) -> str:
    return str(JID("" if user is None else str(user), str(server), device=device, agent=agent, domain_type=_DOMAIN_TYPES.get(str(server), agent or 0)))


def same_user(a: str | JID, b: str | JID) -> bool:
    aa = a if isinstance(a, JID) else parse_jid(a)
    bb = b if isinstance(b, JID) else parse_jid(b)
    return aa.user == bb.user


def transfer_device(source: str | JID, target: str | JID) -> JID:
    src = source if isinstance(source, JID) else parse_jid(source)
    dst = target if isinstance(target, JID) else parse_jid(target)
    return JID(dst.user, dst.server, device=src.device, agent=dst.agent, domain_type=dst.domain_type)
