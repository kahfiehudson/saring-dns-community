"""Unbound Python module: blocks domains (and all their subdomains) using an
LMDB-backed lookup instead of RPZ, to avoid Unbound's RPZ memory blowup at
multi-million-record scale (confirmed OOM at 15GB+ RSS for ~19M RPZ records
on this deployment).

LMDB is memory-mapped - the OS pages it in on demand, so process RSS stays
small regardless of how many domains are in the database. Blocking "all
subdomains too" is done here by checking every parent suffix of the query
name against the same database, so - unlike RPZ - there is no need for a
separate wildcard entry per domain; the database only needs the base domains
(see scripts/build_lmdb_blocklist.py).

Four LMDB databases: the big official list, a small whitelist (checked
first - bypasses everything below), a small custom-block list for
admin-added domains (kept separate from the official list so it isn't wiped
out by the next scheduled rebuild, and so adding one entry doesn't require
re-parsing the ~9.5M-domain source file), and an optional threat-intel feed
(malware/phishing domains, toggled independently in Pengaturan DNS - written
empty when off, so this module always checks it unconditionally rather than
needing its own separate enabled/disabled code path).

Block action (NXDOMAIN vs redirect) is read from action.json, written by
app/unbound_writer.py from the dashboard's block_mode/redirect_ip settings -
this module has no access to the dashboard's SQLite database directly, and
only reads that file once at init_standard() (a fresh Unbound process/config
apply is needed to pick up a changed action, same as everything else here).

Redirect answers a direct A record for the redirect IP - not a CNAME to an
intermediate name - specifically to avoid the resolution-chain problem a
CNAME-based RPZ redirect hit in this same deployment (the CNAME target got
re-resolved as an ordinary internet name instead of using local-data,
because RPZ processing runs before the module holding that local-data).
Answering the final A record directly sidesteps that whole class of bug.

module-config placement: must be literally "python respip validator iterator"
(python first) - Unbound's checkconf only accepts a fixed set of known-good
module orderings; this is the one that includes python + respip + validator
(respip is kept so the small whitelist RPZ zone still works for domains this
module passes through - custom-block is handled entirely here now, not via
RPZ, precisely because of the CNAME-chain issue above).

Top-domains and top-clients tracking: every query name seen (and,
separately, every one that gets blocked) increments an in-process Counter
here, same for the querying client's IP - periodically flushed to a small
JSON file that scripts/collect_stats.py reads and rolls up into the
dashboard's per-day domain/client stats (see app/top_domains.py,
app/top_clients.py) - this module has no access to the dashboard's
database, only a plain file, same pattern as
action.json above. The Counters are capped and truncated between flushes
(see MAX_TRACKED_ENTRIES) so a resolver seeing many millions of distinct
names can't grow this module's memory unbounded between flushes.
"""
import collections
import json
import os
import time
from datetime import datetime, timezone

import lmdb

BLOCK_DB_PATH = "/etc/unbound/trustpositif/lmdb/blocklist"
WHITELIST_DB_PATH = "/etc/unbound/trustpositif/lmdb/whitelist"
CUSTOM_BLOCK_DB_PATH = "/etc/unbound/trustpositif/lmdb/custom_block"
THREAT_INTEL_DB_PATH = "/etc/unbound/trustpositif/lmdb/threat_intel"
ACTION_CONF_PATH = "/etc/unbound/trustpositif/pymod/action.json"
TOPDOMAINS_PATH = "/etc/unbound/trustpositif/pymod/top_domains.json"

FLUSH_INTERVAL_SECONDS = 300
MAX_TRACKED_ENTRIES = 5000  # forces an early truncation if traffic is very high-cardinality
TRUNCATE_TO = 500           # ... down to this many (by count) when that happens
FLUSH_TOP_N = 200           # how many domains actually get written out per flush

_block_env = None
_whitelist_env = None
_custom_block_env = None
_threat_intel_env = None
_action = "nxdomain"
_redirect_ip = ""

_query_counts = collections.Counter()
_block_counts = collections.Counter()
_client_query_counts = collections.Counter()
_client_block_counts = collections.Counter()
_window_start = 0.0
_last_flush = 0.0


def _open_env(path):
    try:
        env = lmdb.open(path, readonly=True, lock=False, max_dbs=0)
        log_info(f"trustpositif_block: opened {path}")
        return env
    except Exception as e:
        log_err(f"trustpositif_block: could not open {path}: {e!r}")
        return None


def init_standard(id, cfg):
    global _block_env, _whitelist_env, _custom_block_env, _threat_intel_env, _action, _redirect_ip
    global _window_start, _last_flush
    _block_env = _open_env(BLOCK_DB_PATH)
    _whitelist_env = _open_env(WHITELIST_DB_PATH)
    _custom_block_env = _open_env(CUSTOM_BLOCK_DB_PATH)
    _threat_intel_env = _open_env(THREAT_INTEL_DB_PATH)
    try:
        with open(ACTION_CONF_PATH) as f:
            conf = json.load(f)
        _action = conf.get("action", "nxdomain")
        _redirect_ip = conf.get("redirect_ip", "")
        log_info(f"trustpositif_block: action={_action} redirect_ip={_redirect_ip!r}")
    except Exception as e:
        log_err(f"trustpositif_block: could not read {ACTION_CONF_PATH}, defaulting to nxdomain: {e}")
        _action = "nxdomain"
        _redirect_ip = ""
    _window_start = _last_flush = time.time()
    return True


def deinit(id):
    return True


def inform_super(id, qstate, superqstate, qdata):
    return True


def _suffixes(qname_str):
    """'a.b.example.com.' -> ['a.b.example.com', 'b.example.com', 'example.com']
    (skips the bare TLD - matches RPZ wildcard semantics: domain + all subdomains)."""
    labels = qname_str.rstrip(".").lower().split(".")
    for i in range(len(labels) - 1):
        yield ".".join(labels[i:])


def _track(counter, key):
    counter[key] += 1
    if len(counter) > MAX_TRACKED_ENTRIES:
        trimmed = counter.most_common(TRUNCATE_TO)
        counter.clear()
        counter.update(dict(trimmed))


def _maybe_flush():
    global _query_counts, _block_counts, _client_query_counts, _client_block_counts
    global _window_start, _last_flush
    now = time.time()
    if now - _last_flush < FLUSH_INTERVAL_SECONDS:
        return
    try:
        payload = {
            "window_start": datetime.fromtimestamp(_window_start, tz=timezone.utc).isoformat(),
            "window_end": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "queries": dict(_query_counts.most_common(FLUSH_TOP_N)),
            "blocks": dict(_block_counts.most_common(FLUSH_TOP_N)),
            "client_queries": dict(_client_query_counts.most_common(FLUSH_TOP_N)),
            "client_blocks": dict(_client_block_counts.most_common(FLUSH_TOP_N)),
        }
        tmp = TOPDOMAINS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, TOPDOMAINS_PATH)
    except Exception as e:
        log_err(f"trustpositif_block: top-domains flush failed: {e!r}")
    _query_counts = collections.Counter()
    _block_counts = collections.Counter()
    _client_query_counts = collections.Counter()
    _client_block_counts = collections.Counter()
    _window_start = now
    _last_flush = now


def _client_ip(qstate):
    """Best-effort client IP for top-clients tracking (see _maybe_flush) -
    internal sub-queries Unbound's iterator issues to itself (root/TLD
    lookups, trust-anchor probes) don't carry a real client reply channel
    (qstate.mesh_info.reply_list is None for those), so this returns None
    whenever that's missing rather than ever raising - a tracking miss
    here must never affect actual query resolution."""
    try:
        if qstate.mesh_info is not None and qstate.mesh_info.reply_list is not None:
            return qstate.mesh_info.reply_list.query_reply.addr
    except Exception:
        pass
    return None


def _matches(env, qname_str):
    if env is None:
        return False
    try:
        with env.begin() as txn:
            for candidate in _suffixes(qname_str):
                if txn.get(candidate.encode("ascii", "ignore")) is not None:
                    return True
    except Exception as e:
        log_err(f"trustpositif_block: lookup error: {e!r}")
    return False


def _respond_nxdomain(id, qstate, qname):
    msg = DNSMessage(qname, qstate.qinfo.qtype, qstate.qinfo.qclass, PKT_QR | PKT_RA | PKT_AA)
    if not msg.set_return_msg(qstate):
        qstate.ext_state[id] = MODULE_ERROR
        return
    qstate.return_msg.rep.security = 2
    qstate.return_rcode = RCODE_NXDOMAIN
    qstate.ext_state[id] = MODULE_FINISHED


def _respond_redirect(id, qstate, qname):
    # Only A queries get a real redirect answer - anything else (AAAA, MX,
    # TXT, ...) falls back to NXDOMAIN since there's nothing sensible to
    # redirect those to.
    try:
        if qstate.qinfo.qtype != RR_TYPE_A or not _redirect_ip:
            _respond_nxdomain(id, qstate, qname)
            return
        msg = DNSMessage(qname, qstate.qinfo.qtype, qstate.qinfo.qclass, PKT_QR | PKT_RA | PKT_AA)
        # answer is a plain list - append() returns None on success just like
        # list.append(); a malformed RR string raises instead of returning
        # falsy, which the outer try/except here already catches.
        msg.answer.append(f"{qname} 300 IN A {_redirect_ip}")
        if not msg.set_return_msg(qstate):
            qstate.ext_state[id] = MODULE_ERROR
            return
        qstate.return_msg.rep.security = 2
        qstate.return_rcode = RCODE_NOERROR
        qstate.ext_state[id] = MODULE_FINISHED
    except Exception as e:
        log_err(f"trustpositif_block: _respond_redirect failed: {e!r}")
        _respond_nxdomain(id, qstate, qname)


def operate(id, event, qstate, qdata):
    if event == MODULE_EVENT_NEW or event == MODULE_EVENT_PASS:
        qname = qstate.qinfo.qname_str
        # Being first in module-config means this module sees every query
        # Unbound's iterator makes internally too (root/TLD delegation
        # lookups, DNSSEC trust-anchor probes...), not just the original
        # client-facing one - A/AAAA is what "domain X got looked up" means
        # to a human reading a top-domains list, so everything else (NS,
        # SOA, DS, DNSKEY, the RFC 8145 trust-anchor NULL probe...) is
        # excluded here rather than polluting the count with resolver
        # plumbing.
        is_client_type = qstate.qinfo.qtype in (RR_TYPE_A, RR_TYPE_AAAA)
        client_ip = _client_ip(qstate) if is_client_type else None
        if is_client_type:
            _track(_query_counts, qname.rstrip(".").lower())
            if client_ip:
                _track(_client_query_counts, client_ip)
        _maybe_flush()

        if _matches(_whitelist_env, qname):
            qstate.ext_state[id] = MODULE_WAIT_MODULE
            return True

        if _matches(_block_env, qname) or _matches(_custom_block_env, qname) or _matches(_threat_intel_env, qname):
            if is_client_type:
                _track(_block_counts, qname.rstrip(".").lower())
                if client_ip:
                    _track(_client_block_counts, client_ip)
            if _action == "redirect":
                _respond_redirect(id, qstate, qname)
            else:
                _respond_nxdomain(id, qstate, qname)
            return True

        qstate.ext_state[id] = MODULE_WAIT_MODULE
        return True

    if event == MODULE_EVENT_MODDONE:
        qstate.ext_state[id] = MODULE_FINISHED
        return True

    log_err("trustpositif_block: bad event")
    qstate.ext_state[id] = MODULE_ERROR
    return True
