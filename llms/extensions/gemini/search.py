"""Local document search and published Search widget configuration.

Search is deliberately independent from Gemini. Documents are split into small heading-aware
sections and indexed by the local database; published widgets only expose a scoped view of that
index and never need a model or an API key.
"""

import copy
import fnmatch
import html
import hashlib
import ipaddress
import json
import re
import secrets
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit


SEARCH_INDEX_VERSION = "4"
SCOPE_FIELDS = ("category", "docType", "status", "locale", "product", "versions", "tags")
THEMES = ("auto", "light", "dark", "nord", "matrix", "soft-pink")
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
LAUNCHER_STYLES = ("raised", "flat", "inset")
SEARCH_GROUP_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "for", "how", "in", "is", "of", "on", "the", "to", "with",
})
DEFAULT_DENIED_USER_AGENTS = (
    "bytespider", "gptbot", "claudebot", "amazonbot", "imagesiftbot", "semrushbot",
    "dotbot", "dataforseobot", "whatsapp bot", "petalbot",
)

DEFAULT_CONFIG = {
    "identity": {
        "title": "Search documentation",
        "placeholder": "Search docs",
        "emptyText": "No matching documents found.",
        "tooltip": "",
    },
    "scope": {},
    "ranking": {
        "titleWeight": 8.0,
        "headingWeight": 5.0,
        "contentWeight": 1.0,
        "phraseBoost": 4.0,
        "exactTitleBoost": 6.0,
        "freshnessWeight": 20.0,
        "freshnessHalfLifeDays": 365,
        "nativeWeight": 2.0,
        "docTypeWeights": {},
    },
    "behavior": {
        "commandKShortcut": True,
        "slashShortcut": True,
        "minChars": 2,
        "maxResults": 30,
        "groupLimit": 8,
    },
    "analytics": {
        "enabled": False,
        "retentionDays": 90,
        "anonymizeIp": True,
        "respectDoNotTrack": True,
        "excludeBots": True,
        "requireConsent": False,
        "deniedUserAgents": list(DEFAULT_DENIED_USER_AGENTS),
        "deniedIpRanges": [],
        "excludedPaths": [],
    },
    "appearance": {
        "theme": "auto",
        "highlightColor": "",
        "fontFamily": "",
        "position": "bottom-right",
        "launcherStyle": "flat",
        "mount": "",
        "offset": {"top": 20, "right": 20, "bottom": 20, "left": 20},
        "width": 420,
        "dialogWidth": 760,
    },
    "hosting": {
        "allowedOrigins": [],
        "requestsPerMinute": 120,
    },
}


def clean_selector(value):
    """Sanitize a host-page CSS selector used to mount the launcher inline.

    Kept independent of assistants.py so this module stays loadable on its own.
    """
    return re.sub(r"[\x00-\x1f<>{};\\]", "", str(value or "")).strip()[:300]


def new_public_id():
    return secrets.token_urlsafe(18).replace("-", "").replace("_", "")


def normalize_search_query(value):
    """Return a stable, accent-insensitive form used to compare search wording."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))[:300]


def _search_token_root(token):
    # A deliberately conservative stemmer: it groups common query variations without making
    # display labels unreadable or requiring a language-specific search dependency.
    for suffix, minimum in (("ations", 7), ("ation", 7), ("ments", 7), ("ment", 7),
                            ("ings", 6), ("ing", 6), ("ies", 5), ("ed", 5), ("es", 5),
                            ("e", 6), ("s", 4)):
        if len(token) >= minimum and token.endswith(suffix):
            return token[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def search_query_group_key(value):
    """Build an order-independent intent key for grouping closely related user searches."""
    normalized = normalize_search_query(value)
    tokens = [_search_token_root(x) for x in normalized.split()
              if x not in SEARCH_GROUP_STOP_WORDS]
    return " ".join(sorted(dict.fromkeys(tokens))) or normalized


def _merge(left, right):
    out = copy.deepcopy(left)
    for key, value in (right or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _bounded_int(value, default, minimum, maximum):
    try:
        return min(max(int(value), minimum), maximum)
    except (TypeError, ValueError):
        return default


def _bounded_float(value, default, minimum, maximum):
    try:
        return min(max(float(value), minimum), maximum)
    except (TypeError, ValueError):
        return default


def _normalize_rules(value, normalize, limit=100, max_length=500):
    values = value if isinstance(value, list) else re.split(r"[,\n]", str(value or ""))
    result = []
    seen = set()
    for item in values:
        normalized = normalize(str(item or "").strip()[:max_length])
        key = normalized.lower() if normalized else ""
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
        if len(result) >= limit:
            break
    return result


def normalize_ip_rule(value):
    value = str(value or "").strip()
    if not value:
        return None
    if "*" in value:
        parts = value.split(".")
        if not 1 <= len(parts) <= 4:
            return None
        parts += ["*"] * (4 - len(parts))
        wildcard = False
        fixed = []
        for part in parts:
            if part == "*":
                wildcard = True
                fixed.append("0")
            elif wildcard:
                return None
            else:
                try:
                    number = int(part)
                except ValueError:
                    return None
                if not 0 <= number <= 255:
                    return None
                fixed.append(str(number))
        prefix = next((i * 8 for i, part in enumerate(parts) if part == "*"), 32)
        value = f"{'.'.join(fixed)}/{prefix}"
    try:
        if "/" in value:
            network = ipaddress.ip_network(value, strict=False)
            return str(network.network_address) if network.prefixlen == network.max_prefixlen else str(network)
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def normalize_path_rule(value):
    value = str(value or "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        value = parsed.path
    if not value.startswith("/"):
        value = "/" + value
    return value


def normalize_ranking(value=None):
    raw = value if isinstance(value, dict) else {}
    defaults = DEFAULT_CONFIG["ranking"]
    raw_types = raw.get("docTypeWeights") if isinstance(raw.get("docTypeWeights"), dict) else {}
    type_weights = {}
    for key, value in list(raw_types.items())[:50]:
        name = str(key or "").strip()[:100]
        weight = _bounded_float(value, 0, -20, 50)
        if name and weight:
            type_weights[name] = weight
    return {
        "titleWeight": _bounded_float(raw.get("titleWeight"), defaults["titleWeight"], 0, 50),
        "headingWeight": _bounded_float(raw.get("headingWeight"), defaults["headingWeight"], 0, 50),
        "contentWeight": _bounded_float(raw.get("contentWeight"), defaults["contentWeight"], 0, 50),
        "phraseBoost": _bounded_float(raw.get("phraseBoost"), defaults["phraseBoost"], 0, 50),
        "exactTitleBoost": _bounded_float(raw.get("exactTitleBoost"), defaults["exactTitleBoost"], 0, 50),
        "freshnessWeight": _bounded_float(raw.get("freshnessWeight"), defaults["freshnessWeight"], 0, 50),
        "freshnessHalfLifeDays": _bounded_int(raw.get("freshnessHalfLifeDays"), defaults["freshnessHalfLifeDays"], 1, 3650),
        "nativeWeight": _bounded_float(raw.get("nativeWeight"), defaults["nativeWeight"], 0, 20),
        "docTypeWeights": type_weights,
    }


def normalize_config(value=None):
    raw = value if isinstance(value, dict) else {}
    config = _merge(DEFAULT_CONFIG, raw)

    identity = config["identity"] if isinstance(config.get("identity"), dict) else {}
    config["identity"] = {
        "title": str(identity.get("title") or DEFAULT_CONFIG["identity"]["title"]).strip()[:200],
        "placeholder": str(identity.get("placeholder") or DEFAULT_CONFIG["identity"]["placeholder"]).strip()[:120],
        "emptyText": str(identity.get("emptyText") or DEFAULT_CONFIG["identity"]["emptyText"]).strip()[:300],
        "tooltip": str(identity.get("tooltip") or "").strip()[:200],
    }

    scope = config["scope"] if isinstance(config.get("scope"), dict) else {}
    config["scope"] = {
        key: str(scope[key]).strip()[:300]
        for key in SCOPE_FIELDS if scope.get(key) not in (None, "")
    }
    config["ranking"] = normalize_ranking(config.get("ranking"))

    behavior = config["behavior"] if isinstance(config.get("behavior"), dict) else {}
    supplied_behavior = raw.get("behavior") if isinstance(raw.get("behavior"), dict) else {}
    legacy_shortcut = supplied_behavior.get("keyboardShortcut")
    config["behavior"] = {
        "commandKShortcut": bool(supplied_behavior.get("commandKShortcut",
                                                       True if legacy_shortcut is None else legacy_shortcut)),
        "slashShortcut": bool(supplied_behavior.get("slashShortcut",
                                                    True if legacy_shortcut is None else legacy_shortcut)),
        "minChars": _bounded_int(behavior.get("minChars"), 2, 1, 10),
        "maxResults": _bounded_int(behavior.get("maxResults"), 30, 5, 100),
        "groupLimit": _bounded_int(behavior.get("groupLimit"), 8, 1, 30),
    }

    analytics = config["analytics"] if isinstance(config.get("analytics"), dict) else {}
    config["analytics"] = {
        "enabled": bool(analytics.get("enabled", False)),
        "retentionDays": _bounded_int(analytics.get("retentionDays"), 90, 1, 3650),
        "anonymizeIp": bool(analytics.get("anonymizeIp", True)),
        "respectDoNotTrack": bool(analytics.get("respectDoNotTrack", True)),
        "excludeBots": bool(analytics.get("excludeBots", True)),
        "requireConsent": bool(analytics.get("requireConsent", False)),
        "deniedUserAgents": _normalize_rules(
            analytics.get("deniedUserAgents"), lambda x: x.lower(), max_length=200),
        "deniedIpRanges": _normalize_rules(
            analytics.get("deniedIpRanges"), normalize_ip_rule, max_length=100),
        "excludedPaths": _normalize_rules(
            analytics.get("excludedPaths"), normalize_path_rule),
    }

    appearance = config["appearance"] if isinstance(config.get("appearance"), dict) else {}
    highlight_color = str(appearance.get("highlightColor") or "").strip()
    font_family = re.sub(r"[\x00-\x1f{};]", "", str(appearance.get("fontFamily") or "")).strip()[:300]
    offset = appearance.get("offset") if isinstance(appearance.get("offset"), dict) else {}
    config["appearance"] = {
        "theme": appearance.get("theme") if appearance.get("theme") in THEMES else "auto",
        "highlightColor": highlight_color if re.fullmatch(r"#[0-9a-fA-F]{6}", highlight_color) else "",
        "fontFamily": font_family,
        "position": appearance.get("position") if appearance.get("position") in POSITIONS else "bottom-right",
        "launcherStyle": appearance.get("launcherStyle") if appearance.get("launcherStyle") in LAUNCHER_STYLES else "flat",
        "mount": clean_selector(appearance.get("mount")),
        "offset": {
            side: _bounded_int(offset.get(side), 20, 0, 400)
            for side in ("top", "right", "bottom", "left")
        },
        "width": _bounded_int(appearance.get("width"), 420, 240, 900),
        "dialogWidth": _bounded_int(appearance.get("dialogWidth"), 760, 420, 1200),
    }

    hosting = config["hosting"] if isinstance(config.get("hosting"), dict) else {}
    origins = hosting.get("allowedOrigins") or []
    if not isinstance(origins, list):
        origins = re.split(r"[,\n]", str(origins))
    config["hosting"] = {
        "allowedOrigins": list(dict.fromkeys(
            str(x).strip().rstrip("/") for x in origins if str(x).strip()
        ))[:100],
        "requestsPerMinute": _bounded_int(hosting.get("requestsPerMinute"), 120, 1, 5000),
    }
    return config


BOT_USER_AGENT = re.compile(
    r"bot|crawler|spider|slurp|bingpreview|headlesschrome|lighthouse|pagespeed|"
    r"uptimerobot|pingdom|statuscake|facebookexternalhit|twitterbot|linkedinbot|"
    r"whatsapp|google-inspectiontool",
    re.IGNORECASE,
)


def is_bot(user_agent):
    """Conservatively identify common crawlers without rejecting normal browsers."""
    return bool(BOT_USER_AGENT.search(str(user_agent or "")))


def is_denied_user_agent(user_agent, rules):
    value = str(user_agent or "").lower()
    return bool(value and any(str(rule or "").strip().lower() in value for rule in rules or []
                              if str(rule or "").strip()))


def is_denied_ip(value, rules):
    try:
        address = ipaddress.ip_address(str(value or "").strip())
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
    except ValueError:
        return False
    for raw_rule in rules or []:
        rule = normalize_ip_rule(raw_rule)
        if not rule:
            continue
        try:
            if "/" in rule and address in ipaddress.ip_network(rule, strict=False):
                return True
            if "/" not in rule and address == ipaddress.ip_address(rule):
                return True
        except (TypeError, ValueError):
            continue
    return False


def is_excluded_path(value, rules):
    value = str(value or "").strip()
    if not value:
        return False
    parsed = urlsplit(value)
    path = parsed.path if parsed.scheme in ("http", "https") and parsed.netloc else value.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return any(fnmatch.fnmatchcase(path.lower(), rule.lower()) for rule in
               (normalize_path_rule(x) for x in rules or []) if rule)


def rank_results(rows, query, documents=None, ranking=None):
    """Apply one transparent ranking model after each RDBMS finds matching candidates."""
    rows = list(rows or [])
    if not rows:
        return rows
    config = normalize_ranking(ranking)
    normalized_query = normalize_search_query(query)
    tokens = list(dict.fromkeys(normalized_query.split()))
    documents = documents or {}
    now = time.time()

    def quality(value):
        text = normalize_search_query(value)
        if not text or not tokens:
            return 0.0, False, False
        coverage = sum(1 for token in tokens if token in text) / len(tokens)
        return coverage, normalized_query in text, text == normalized_query

    count = len(rows)
    ranked = []
    for position, row in enumerate(rows):
        title_quality, title_phrase, exact_title = quality(row.get("documentTitle"))
        heading_quality, heading_phrase, _ = quality(row.get("heading"))
        content_quality, content_phrase, _ = quality(row.get("content"))
        score = (
            config["titleWeight"] * title_quality
            + config["headingWeight"] * heading_quality
            + config["contentWeight"] * content_quality
        )
        if title_phrase or heading_phrase or content_phrase:
            score += config["phraseBoost"]
        if exact_title:
            score += config["exactTitleBoost"]

        document = documents.get(int(row.get("documentId") or 0)) or {}
        updated = document.get("sourceUpdatedAt")
        try:
            updated = float(updated)
        except (TypeError, ValueError):
            updated = 0
        if updated <= 0:
            fallback = document.get("uploadedAt") or document.get("createdAt")
            if isinstance(fallback, datetime):
                updated = fallback.timestamp()
            elif fallback:
                try:
                    updated = datetime.fromisoformat(str(fallback).replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    updated = 0
        if updated > 0 and config["freshnessWeight"]:
            age_days = max(0, now - updated) / 86400
            score += config["freshnessWeight"] * (0.5 ** (age_days / config["freshnessHalfLifeDays"]))

        doc_type = str(row.get("docType") or document.get("docType") or "")
        score += config["docTypeWeights"].get(doc_type, 0)
        # Provider relevance is deliberately rank-normalized: bm25, ts_rank, CONTAINSTABLE and
        # MATCH all use incompatible scales and even disagree on whether lower or higher is best.
        native_quality = 1.0 if count == 1 else 1.0 - position / (count - 1)
        score += config["nativeWeight"] * native_quality
        row = dict(row)
        row["score"] = round(score, 6)
        ranked.append((score, position, row))
    return [row for _, _, row in sorted(ranked, key=lambda item: (-item[0], item[1]))]


def validate_config(value=None):
    config = normalize_config(value)
    for origin in config["hosting"]["allowedOrigins"]:
        if origin == "*":
            continue
        try:
            parsed = urlsplit(origin)
            host = parsed.hostname or ""
            valid = (parsed.scheme in ("http", "https") and host
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                     and ("*" not in host or host.startswith("*.") and host.count("*") == 1))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                f"Invalid allowed origin '{origin}'. Use an exact HTTP(S) origin or a wildcard subdomain."
            )
    return config


def public_config(widget, base_url):
    config = normalize_config(widget.get("config"))
    return {
        "searchId": widget["publicId"],
        **config["identity"],
        "behavior": config["behavior"],
        "appearance": config["appearance"],
        "analyticsEnabled": config["analytics"]["enabled"],
        "analytics": {
            "respectDoNotTrack": config["analytics"]["respectDoNotTrack"],
            "requireConsent": config["analytics"]["requireConsent"],
        },
        "searchUrl": f"{base_url}/ext/gemini/public/searches/{widget['publicId']}/results",
        "clickUrl": f"{base_url}/ext/gemini/public/searches/{widget['publicId']}/clicks",
        "analyticsUrl": f"{base_url}/ext/gemini/public/searches/{widget['publicId']}/pageviews",
    }


def desired_hash(document):
    """Signature of everything capable of changing locally rendered search results."""
    value = {
        "contentHash": document.get("contentHash") or document.get("hash"),
        "metadataHash": document.get("metadataHash"),
        "displayName": document.get("displayName"),
        "sourceUrl": document.get("sourceUrl"),
        "extractorVer": document.get("extractorVer"),
        "category": document.get("category"),
        "docType": document.get("docType"),
        "status": document.get("status"),
        "locale": document.get("locale"),
        "product": document.get("product"),
        "versions": document.get("versions"),
        "tags": document.get("tags"),
        "indexVersion": SEARCH_INDEX_VERSION,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def slugify(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"<[^>]+>", "", value).lower()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    return re.sub(r"[-\s]+", "-", value).strip("-")


def clean_search_markdown(value):
    """Remove presentation markup and fenced code from text used by Search previews."""
    def clean_fragment(fragment):
        inline_code = []

        def protect_code(match):
            inline_code.append(match.group(1))
            return f"\x01CODE{len(inline_code) - 1}\x02"

        fragment = re.sub(r"`([^`\n]*)`", protect_code, fragment)
        fragment = re.sub(r"(?m)^\s*:{3,}.*$", "", fragment)
        fragment = re.sub(r"<!--[\s\S]*?-->", " ", fragment)
        fragment = re.sub(
            r"<(script|style|noscript|svg|form|iframe)\b[^>]*>[\s\S]*?</\1\s*>",
            " ", fragment, flags=re.I,
        )

        def heading(match):
            level = int(match.group(1))
            content = re.sub(r"<[^>]+>", " ", match.group(2))
            content = re.sub(r"\s+", " ", content).strip()
            return f"\n{'#' * level} {content}\n"

        fragment = re.sub(r"<h([1-6])\b[^>]*>([\s\S]*?)</h\1\s*>", heading, fragment, flags=re.I)
        fragment = re.sub(
            r"</?(?:address|article|aside|blockquote|div|dl|dt|dd|fieldset|figcaption|figure|footer|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>|<br\s*/?>",
            "\n", fragment, flags=re.I,
        )
        fragment = re.sub(r"<[^>]+>", " ", fragment)
        fragment = html.unescape(fragment)
        for index, code in enumerate(inline_code):
            fragment = fragment.replace(f"\x01CODE{index}\x02", code)
        return fragment

    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)", str(value or ""))
    return "".join("\n" if index % 2 else clean_fragment(part) for index, part in enumerate(parts))


def _plain(value, preserve_underscores=False):
    value = re.sub(r"`([^`]*)`", r"\1", str(value or ""))
    value = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*~]", "", value)
    if not preserve_underscores:
        value = value.replace("_", "")
    return re.sub(r"\s+", " ", value).strip()


def split_sections(text, document, chunk_chars=1400, document_title=None):
    """Turn normalized Markdown-ish text into heading-aware, bounded search rows."""
    text = clean_search_markdown(text)
    title = _plain(
        document_title or document.get("displayName") or document.get("sourceKey") or "Document",
        preserve_underscores=True,
    )
    title = re.sub(r"\.(?:md|mdx|markdown|html?|txt)$", "", title, flags=re.I)
    base_url = document.get("sourceUrl") or document.get("url") or ""
    headings = []
    anchors = {}
    rows = []
    paragraph = []
    ordinal = 0

    def current_heading():
        return headings[-1][1] if headings else title

    def current_anchor():
        return headings[-1][2] if headings else ""

    def append(content="", kind="content"):
        nonlocal ordinal
        content = _plain(content)
        heading = current_heading()
        if not content and kind == "content":
            return
        anchor = current_anchor()
        url = base_url + (f"#{anchor}" if anchor and base_url else "")
        hierarchy = [item[1] for item in headings]
        rows.append({
            "ordinal": ordinal,
            "documentTitle": title,
            "heading": heading,
            "headingLevel": headings[-1][0] if headings else 0,
            "hierarchy": hierarchy,
            "anchor": anchor or None,
            "url": url,
            "kind": "doc" if not headings else kind,
            "content": content,
        })
        ordinal += 1

    def flush():
        nonlocal paragraph
        content = " ".join(paragraph).strip()
        paragraph = []
        while len(content) > chunk_chars:
            cut = content.rfind(" ", 0, chunk_chars)
            cut = cut if cut > chunk_chars // 2 else chunk_chars
            append(content[:cut])
            content = content[cut:].strip()
        append(content)

    in_fence = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            paragraph.append(line)
            continue
        match = None if in_fence else re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading = _plain(match.group(2))
            while headings and headings[-1][0] >= level:
                headings.pop()
            base = slugify(heading) or f"section-{ordinal + 1}"
            number = anchors.get(base, 0)
            anchors[base] = number + 1
            anchor = base if number == 0 else f"{base}-{number}"
            headings.append((level, heading, anchor))
            append("", "heading")
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
    flush()
    if not rows:
        append(str(text or ""))
    return rows
