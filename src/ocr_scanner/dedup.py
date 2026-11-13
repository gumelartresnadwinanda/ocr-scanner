"""Smart spatio-temporal, normalized, and handle-aware deduplication."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .grouping import extract_handles
from .models import BlockObservation, DistinctTextEntry

_SPLIT_HANDLE_RE = re.compile(r"^@\s+([A-Za-z0-9_.]{2,})$")
_DIGITS_RE = re.compile(r"\d+")
_SINGLE_TOKEN_RE = re.compile(r"^@?[A-Za-z0-9_.]{3,}$")
_EDGE_PUNCT_STRIP = " \t\n\r.,;:|~"


def normalize_text_for_match(text: str) -> str:
    """Normalize whitespace, stray split-@ spaces, and case for exact matching."""
    lines = [" ".join(line.split()) for line in text.strip().splitlines() if line.strip()]
    joined = " ".join(lines)
    split_match = _SPLIT_HANDLE_RE.match(joined)
    if split_match:
        joined = f"@{split_match.group(1)}"
    return joined.casefold()


def normalize_display_text(text: str) -> str:
    """Clean minor whitespace artifacts while preserving line breaks and casing."""
    lines = [" ".join(line.split()) for line in text.strip().splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    split_match = _SPLIT_HANDLE_RE.match(cleaned)
    if split_match:
        cleaned = f"@{split_match.group(1)}"
    return cleaned


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / (area_a + area_b - inter)


def _boxes_same_region(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    frame_w: int,
    frame_h: int,
) -> bool:
    """Return True if two bounding boxes occupy roughly the same screen region."""
    if _bbox_iou(a, b) >= 0.25:
        return True
    diag = max(1.0, (frame_w**2 + frame_h**2) ** 0.5)
    acx, acy = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bcx, bcy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    dist_norm = (((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5) / diag

    ah = max(1.0, a[3] - a[1])
    bh = max(1.0, b[3] - b[1])
    height_ratio = max(ah, bh) / min(ah, bh)
    return dist_norm <= 0.10 and height_ratio <= 1.6


def _can_fuzzy_merge(text_a: str, text_b: str, min_ratio: float = 88.0) -> bool:
    """Guard fuzzy merging so distinct numbered handles (e.g. @user1 vs @user2) never collide."""
    cmp_a = normalize_text_for_match(text_a).strip(_EDGE_PUNCT_STRIP)
    cmp_b = normalize_text_for_match(text_b).strip(_EDGE_PUNCT_STRIP)
    if cmp_a == cmp_b:
        return True
    if min(len(cmp_a), len(cmp_b)) < 4:
        return False

    digits_a = _DIGITS_RE.findall(cmp_a)
    digits_b = _DIGITS_RE.findall(cmp_b)
    if (digits_a or digits_b) and digits_a != digits_b:
        return False

    return fuzz.ratio(cmp_a, cmp_b) >= min_ratio


@dataclass
class _Cluster:
    observations: list[BlockObservation] = field(default_factory=list)
    norm_keys: set[str] = field(default_factory=set)

    @property
    def first_seen_sec(self) -> float:
        return min(obs.timestamp_sec for obs in self.observations)

    @property
    def last_seen_sec(self) -> float:
        return max(obs.timestamp_sec for obs in self.observations)

    @property
    def latest_box(self) -> tuple[float, float, float, float]:
        latest = max(self.observations, key=lambda o: o.timestamp_sec)
        b = latest.block
        return (b.x1, b.y1, b.x2, b.y2)

    def add(self, obs: BlockObservation) -> None:
        self.observations.append(obs)
        self.norm_keys.add(normalize_text_for_match(obs.block.text))

    def merge_from(self, other: _Cluster) -> None:
        self.observations.extend(other.observations)
        self.norm_keys.update(other.norm_keys)

    def canonical_text(self) -> str:
        """Choose the cleanest representative text among merged variants."""
        has_at_variant = any(normalize_display_text(o.block.text).startswith("@") for o in self.observations)
        stats: dict[str, list[float]] = {}
        for obs in self.observations:
            disp = normalize_display_text(obs.block.text)
            entry = stats.setdefault(disp, [0.0, 0.0])
            entry[0] += 1.0
            entry[1] = max(entry[1], obs.block.confidence)

        def rank(item: tuple[str, list[float]]) -> tuple[int, int, int, float, int]:
            txt, (count, max_conf) = item
            not_truncated = 0 if txt.endswith("..") else 1
            starts_at = 1 if (has_at_variant and txt.startswith("@")) else 0
            return (not_truncated, starts_at, int(count), max_conf, len(txt))

        return max(stats.items(), key=rank)[0]

    def best_observation(self, canonical: str, frame_w: int, frame_h: int) -> BlockObservation:
        """Select the sharpest, unclipped frame example representing canonical text."""
        canon_norm = normalize_text_for_match(canonical)

        def score(obs: BlockObservation) -> tuple[int, int, float, float]:
            disp = normalize_display_text(obs.block.text)
            exact_match = 1 if normalize_text_for_match(disp) == canon_norm else 0
            not_truncated = 0 if disp.endswith("..") else 1
            b = obs.block
            clipped = b.x1 <= 1.5 or b.y1 <= 1.5 or b.x2 >= frame_w - 1.5 or b.y2 >= frame_h - 1.5
            conf = b.confidence - (0.08 if clipped else 0.0)
            return (exact_match, not_truncated, conf, -obs.timestamp_sec)

        return max(self.observations, key=score)


def _merge_handle_and_truncated_variants(clusters: list[_Cluster]) -> list[_Cluster]:
    """Post-pass: merge bare handles into @handle and UI ellipsis prefixes into full strings."""
    if len(clusters) <= 1:
        return clusters

    canonicals = [c.canonical_text() for c in clusters]
    norms = [normalize_text_for_match(t) for t in canonicals]

    # 1. Merge bare 'username' into '@username' if '@username' is present in the video
    at_handle_map: dict[str, int] = {}
    for idx, norm in enumerate(norms):
        if norm.startswith("@") and _SINGLE_TOKEN_RE.match(norm) and not norm.endswith(".."):
            at_handle_map.setdefault(norm.lstrip("@"), idx)

    merged_into: dict[int, int] = {}
    for idx, norm in enumerate(norms):
        if not norm.startswith("@") and _SINGLE_TOKEN_RE.match(norm):
            target = at_handle_map.get(norm)
            if target is not None and target != idx:
                merged_into[idx] = target

    # 2. Merge UI-truncated prefixes like '@rosiesapothecar...' into '@rosiesapothecary'
    for idx, norm in enumerate(norms):
        if idx in merged_into:
            continue
        if norm.endswith(".."):
            prefix = norm.rstrip(".")
            if len(prefix.lstrip("@")) < 6:
                continue
            for target, target_norm in enumerate(norms):
                if target == idx or target in merged_into or target_norm.endswith(".."):
                    continue
                if target_norm.startswith(prefix) or (
                    _SINGLE_TOKEN_RE.match(prefix)
                    and _SINGLE_TOKEN_RE.match(target_norm)
                    and target_norm.lstrip("@").startswith(prefix.lstrip("@"))
                ):
                    merged_into[idx] = target
                    break

    if not merged_into:
        return clusters

    surviving: list[_Cluster] = []
    for idx, cluster in enumerate(clusters):
        if idx in merged_into:
            target = merged_into[idx]
            while target in merged_into:
                target = merged_into[target]
            clusters[target].merge_from(cluster)
        else:
            surviving.append(cluster)
    return surviving


def deduplicate_observations(
    observations: list[BlockObservation],
    frame_w: int,
    frame_h: int,
    time_window_sec: float = 2.5,
    fuzzy_threshold: float = 88.0,
) -> list[tuple[_Cluster, str, BlockObservation]]:
    """Cluster raw frame observations into distinct entries with canonical text & best example."""
    clusters: list[_Cluster] = []
    exact_index: dict[str, _Cluster] = {}

    for obs in sorted(observations, key=lambda o: (o.frame_index, o.block.y1, o.block.x1)):
        norm = normalize_text_for_match(obs.block.text)
        if not norm:
            continue

        # Stage 1: global normalized exact match
        existing = exact_index.get(norm)
        if existing is not None:
            existing.add(obs)
            continue

        # Stage 2: spatio-temporal fuzzy match in same screen region
        obs_box = (obs.block.x1, obs.block.y1, obs.block.x2, obs.block.y2)
        matched_cluster: _Cluster | None = None
        for cluster in reversed(clusters):
            if abs(obs.timestamp_sec - cluster.last_seen_sec) > time_window_sec:
                continue
            if not _boxes_same_region(obs_box, cluster.latest_box, frame_w, frame_h):
                continue
            rep_text = cluster.observations[-1].block.text
            if _can_fuzzy_merge(obs.block.text, rep_text, min_ratio=fuzzy_threshold):
                matched_cluster = cluster
                break

        if matched_cluster is not None:
            matched_cluster.add(obs)
            exact_index[norm] = matched_cluster
        else:
            new_cluster = _Cluster()
            new_cluster.add(obs)
            clusters.append(new_cluster)
            exact_index[norm] = new_cluster

    # Stage 3: merge missing-@ bare handles and ellipsis-truncated prefixes
    clusters = _merge_handle_and_truncated_variants(clusters)

    results: list[tuple[_Cluster, str, BlockObservation]] = []
    for cluster in sorted(clusters, key=lambda c: (c.first_seen_sec, c.best_observation(c.canonical_text(), frame_w, frame_h).block.y1)):
        canonical = cluster.canonical_text()
        best_obs = cluster.best_observation(canonical, frame_w, frame_h)
        results.append((cluster, canonical, best_obs))
    return results


def build_distinct_entries(
    clustered: list[tuple[_Cluster, str, BlockObservation]],
    clean_frame_paths: dict[int, str],
    boxed_frame_paths: dict[int, str],
    last_seen_overrides: dict[int, float] | None = None,
) -> list[DistinctTextEntry]:
    """Convert clustered observations into serialized DistinctTextEntry records."""
    entries: list[DistinctTextEntry] = []
    for idx, (cluster, canonical, best_obs) in enumerate(clustered, start=1):
        timestamps = sorted({round(o.timestamp_sec, 3) for o in cluster.observations})
        last_seen = cluster.last_seen_sec
        if last_seen_overrides and best_obs.frame_index in last_seen_overrides:
            last_seen = max(last_seen, last_seen_overrides[best_obs.frame_index])
        if last_seen_overrides:
            for o in cluster.observations:
                if o.frame_index in last_seen_overrides:
                    last_seen = max(last_seen, last_seen_overrides[o.frame_index])

        handles = extract_handles(canonical)
        is_handle = canonical.strip().startswith("@") and len(handles) == 1 and " " not in canonical.strip()
        b = best_obs.block
        entries.append(
            DistinctTextEntry(
                id=idx,
                text=canonical,
                is_handle=is_handle,
                handles=handles,
                confidence=best_obs.block.confidence,
                first_seen_sec=cluster.first_seen_sec,
                last_seen_sec=last_seen,
                occurrences=len(cluster.observations),
                timestamps_sec=timestamps,
                best_frame_index=best_obs.frame_index,
                best_timestamp_sec=best_obs.timestamp_sec,
                best_bbox=(int(round(b.x1)), int(round(b.y1)), int(round(b.x2)), int(round(b.y2))),
                clean_frame_path=clean_frame_paths[best_obs.frame_index],
                boxed_frame_path=boxed_frame_paths[idx],
            )
        )
    return entries
