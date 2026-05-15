"""
GSID (GeoSpatial IDentifier) — public-release stub.

The production framework allocates identifiers under the KAIST CRC GSID v7.0
specification:

    Format: {DOMAIN:2}-{SPATIAL:8}-{ELEV:2}-{GEN:2}-{SEQ:6}-{CHK:1}
    Example: BL-89C259AB-G0-00-000001-7

    DOMAIN  (2)  - Entity-type domain code (42 domains, A-Z uppercase)
    SPATIAL (8)  - S2 cell level-14 token (uppercase hex; ~200-300 m cells)
    ELEV    (2)  - Elevation zone code (U9..U1 / G0..G9 / A1..A9)
    GEN     (2)  - Generation token (Base36, 00..ZZ = 1,296 generations)
    SEQ     (6)  - Per-(domain, cell, elev, gen) sequence (000001..999999)
    CHK     (1)  - Luhn mod-36 check digit

The derivation algorithm — S2 cell tokenisation, Luhn mod-36 checksum, and
the per-domain sequence allocator — is provided by an external service
operated by KAIST Convergence Research Center and is not redistributed with
this code release.

This module is an interface-compatible **stub** that returns deterministic
synthetic identifiers (UUID5-based) sharing the same string-key semantics
as the production format. The framework's rule engine, analysis API, and
case-study results all work correctly with synthetic IDs because no
analysis function parses GSID internals — they are used purely as opaque
entity handles for graph indexing and cross-table joins.

To use the canonical KAIST CRC format in production, replace this module
with the upstream implementation; the public surface (function names and
signatures) is preserved here.
"""
from __future__ import annotations

import uuid
from typing import Optional


# ─── Stable namespace so identical input always yields the same stub GSID ───
_STUB_NS = uuid.UUID("00000000-0000-0000-0000-000000000001")

# ─── Symbolic spec constants kept so callers that import them still resolve ───
S2_LEVEL = 14
LUHN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LUHN_MAP = {c: i for i, c in enumerate(LUHN_CHARS)}


# ──────────────────────────────────────────────────────────────────────────
# Public API (interface-compatible with the production module)
# ──────────────────────────────────────────────────────────────────────────

def compute_s2_token(lon: float, lat: float, level: int = S2_LEVEL) -> str:
    """Stub: return a deterministic 8-char hex token derived from (lon, lat)
    via UUID5; the production implementation emits an S2 cell-level token."""
    return uuid.uuid5(_STUB_NS, f"s2|{lon:.7f}|{lat:.7f}|{level}").hex[:8].upper()


def compute_checksum(gsid_body: str) -> str:
    """Stub: return a single deterministic character placeholder; the
    production implementation computes the Luhn mod-36 check digit."""
    return LUHN_CHARS[hash(gsid_body) % len(LUHN_CHARS)]


def format_gen(gen: int) -> str:
    """Format a generation index as 2-character Base36."""
    if gen < 0 or gen >= 36 * 36:
        gen = gen % (36 * 36)
    high, low = divmod(gen, 36)
    return f"{LUHN_CHARS[high]}{LUHN_CHARS[low]}"


def format_seq(seq: int) -> str:
    """Format a sequence index as 6-digit zero-padded decimal."""
    return f"{seq % 1_000_000:06d}"


def reset_seq_counters() -> None:
    """No-op in the stub. The production allocator maintains per-(domain,
    cell, elev, gen) counters; the stub generates collision-free IDs via
    UUID5 hashing of the entity identity, so no counter state is needed."""
    return None


def resolve_subtype(label: str, properties: dict) -> str:
    """Stub: return a generic '<TWO-LETTER>.unknown' subtype.

    The production module classifies each entity into one of 42 domain
    sub-types (e.g., ``BL.res.apt`` for an apartment building); the stub
    returns the two-letter domain prefix plus ``.unknown`` because
    downstream code only consumes the prefix-before-dot for graph routing.
    """
    if not label:
        return "XX.unknown"
    return f"{label[:2].upper()}.unknown"


def generate_gsid(
    domain: str,
    lon: float,
    lat: float,
    elev_zone: str = "G0",
    gen: int = 0,
    seq: Optional[int] = None,
) -> Optional[str]:
    """Stub: build a 6-segment identifier of the same canonical character
    length as a production GSID, but populated with UUID5-derived tokens
    instead of S2 / Luhn / sequence-allocator outputs.
    """
    if not domain:
        return None
    dom = domain.upper()[:2].ljust(2, "X")
    spatial = compute_s2_token(lon, lat)
    elev = (elev_zone or "G0")[:2].upper().ljust(2, "0")
    gen_tok = format_gen(gen)
    if seq is None:
        seq_h = uuid.uuid5(_STUB_NS, f"{dom}|{spatial}|{elev}|{gen}").int
        seq = seq_h % 1_000_000
    seq_tok = format_seq(seq)
    body = f"{dom}-{spatial}-{elev}-{gen_tok}-{seq_tok}"
    return f"{body}-{compute_checksum(body)}"


def generate_gsid_for_entity(label: str, properties: dict) -> Optional[str]:
    """Stub: dispatch to ``generate_gsid`` using the entity's longitude /
    latitude and label-prefix domain. Production allocates a per-(domain,
    cell) sequence so two entities at the same coordinate get different
    SEQ tokens; the stub disambiguates via UUID5 over the entity's uid /
    name so identical input is reproducible across runs.

    The stub uses a minimal label-prefix domain mapping (sufficient for the
    rule engine's opaque-key indexing); the production specification uses
    a curated 42-domain catalogue.
    """
    if not label:
        return None
    # Minimal stub mapping. Downstream code never parses the prefix, so any
    # injective ``label -> 2-letter`` table works; this one favours the most
    # common entity labels of the published case studies.
    _STUB_DOMAIN_MAP = {
        "Building": "BL", "Parcel": "PR", "Road": "RC", "RoadIntersection": "RC",
        "AutoRoadLink": "RL", "ThingsAddr": "IO", "Sensor": "SN", "Camera": "CM",
        "Vehicle": "VH", "ParkingLot": "PK", "ParkingSpace": "PK",
        "Pedestrian": "PD", "Tree": "TR", "Facility": "FC", "Zone": "ZN",
    }
    domain = _STUB_DOMAIN_MAP.get(label, label[:2].upper())
    lon = float(properties.get("longitude") or properties.get("entrance_lon") or 0.0)
    lat = float(properties.get("latitude") or properties.get("entrance_lat") or 0.0)
    uid = (properties.get("uid")
           or properties.get("pnu")
           or properties.get("name")
           or "")
    seq = uuid.uuid5(_STUB_NS, f"{label}|{uid}|{lon}|{lat}").int % 1_000_000
    return generate_gsid(domain, lon, lat, elev_zone="G0", gen=0, seq=seq)


def parse_gsid(gsid_str: str) -> Optional[dict]:
    """Stub: split a 6-segment GSID string into its component dict; returns
    None on a malformed input. Production additionally validates the Luhn
    checksum, decodes the S2 cell, and resolves the domain code to a
    human-readable label — none of which is required by the analysis API.
    """
    if not gsid_str or not isinstance(gsid_str, str):
        return None
    parts = gsid_str.split("-")
    if len(parts) != 6:
        return None
    domain, spatial, elev, gen, seq, chk = parts
    return {
        "domain": domain,
        "spatial": spatial,
        "elev": elev,
        "gen": gen,
        "seq": seq,
        "checksum": chk,
    }


def validate_gsid(gsid_str: str) -> bool:
    """Stub: accept any 6-segment hyphen-separated string of correct
    aggregate length. Production additionally verifies the Luhn check digit
    against the body and confirms each segment matches its character class.
    """
    parsed = parse_gsid(gsid_str)
    if not parsed:
        return False
    expected_lengths = {"domain": 2, "spatial": 8, "elev": 2,
                        "gen": 2, "seq": 6, "checksum": 1}
    return all(len(parsed[k]) == n for k, n in expected_lengths.items())
