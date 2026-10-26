"""Keyless GovInfo BILLSTATUS introduction dates and official display titles.

Bootstrap: python scripts/build_bill_reference.py --max-seconds 1800
Daily refresh: completed Congress/type archives are never fetched again; the
current Congress's directory listings are checked for missing numbers only.
The tables travel with filings.db in the existing CI release asset.
"""

import io
import re
import sqlite3
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from functools import lru_cache
from xml.etree import ElementTree as ET

import httpx

from db import get_db

SOURCE = 'https://www.govinfo.gov/bulkdata/BILLSTATUS'
BILL_TYPES = {
    'hr': 'H.R.', 's': 'S.', 'hres': 'H.Res.', 'sres': 'S.Res.',
    'hjres': 'H.J.Res.', 'sjres': 'S.J.Res.',
    'hconres': 'H.Con.Res.', 'sconres': 'S.Con.Res.',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS bill_reference (
    congress INTEGER NOT NULL,
    type TEXT NOT NULL,
    number INTEGER NOT NULL,
    introduced_date TEXT NOT NULL,
    title TEXT NOT NULL,
    PRIMARY KEY (congress, type, number)
);
CREATE TABLE IF NOT EXISTS bill_reference_sync (
    congress INTEGER NOT NULL,
    type TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (congress, type)
);
CREATE TABLE IF NOT EXISTS public_law_reference (
    congress INTEGER NOT NULL,
    number INTEGER NOT NULL,
    bill_type TEXT NOT NULL,
    bill_number INTEGER NOT NULL,
    PRIMARY KEY (congress, number)
);
'''
# Public law -> enacting bill, from each law's GovInfo MODS record (keyless).
# Laws are numbered consecutively within a Congress, so the refresh probes
# upward from the highest number held until the first law not yet published.
LAW_SOURCE = 'https://www.govinfo.gov/metadata/pkg/PLAW-{congress}publ{number}/mods.xml'
LAW_BATCH = 16


def read_reference(conn):
    try:
        rows = conn.execute('SELECT congress, type, number, introduced_date, title FROM bill_reference')
        return {(c, t, n): (d, title) for c, t, n, d, title in rows}
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        return {}


@lru_cache(maxsize=1)
def load_reference():
    with get_db() as conn:
        return read_reference(conn)


def read_public_laws(conn):
    """{(congress, law number): (congress, bill type, bill number)}."""
    try:
        rows = conn.execute('SELECT congress, number, bill_type, bill_number FROM public_law_reference')
        return {(c, n): (c, t, b) for c, n, t, b in rows}
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
        return {}


@lru_cache(maxsize=1)
def load_public_laws():
    with get_db() as conn:
        return read_public_laws(conn)


def parse_law_mods(xml, congress):
    """The enacting bill named as the law's primary legislative history."""
    bills = [el for el in ET.fromstring(xml).iter()
             if el.tag.rsplit('}', 1)[-1] == 'bill' and el.get('priority') == 'primary']
    bills.sort(key=lambda el: el.get('context') != 'legislative history')
    for el in bills:
        kind, number = (el.get('type') or '').lower(), el.get('number') or ''
        if kind in BILL_TYPES and number.isdigit() and el.get('congress') == str(congress):
            return kind, int(number)
    return None


def _fetch_law(client, congress, number, deadline):
    """(bill type, number), None when unpublished, or raise on an outage."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Bill reference refresh time budget exhausted')
    url = LAW_SOURCE.format(congress=congress, number=number)
    response = client.get(url, timeout=min(10, remaining), follow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 404):
        return None  # GovInfo redirects unknown packages to its error page
    response.raise_for_status()
    return parse_law_mods(response.content, congress) or ('', 0)


def refresh_public_laws(conn, client, pool, congresses, deadline, log=print):
    added = 0
    known = read_public_laws(conn)
    for congress in congresses:
        number = max((n for c, n in known if c == congress), default=0) + 1
        try:
            while True:
                batch = list(range(number, number + LAW_BATCH))
                results = list(pool.map(lambda n: _fetch_law(client, congress, n, deadline), batch))
                found = []
                for n, bill in zip(batch, results):
                    if bill is None:
                        break
                    found.append((congress, n) + bill)
                # A law whose record names no enacting bill is kept as a
                # placeholder ('', 0) so the probe doesn't stall on it.
                conn.executemany('INSERT OR IGNORE INTO public_law_reference VALUES (?, ?, ?, ?)', found)
                conn.commit()
                added += len(found)
                if len(found) < len(batch):
                    break
                number += LAW_BATCH
        except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError) as exc:
            log(f'  Warning: public laws {congress}: {exc}')
    load_public_laws.cache_clear()
    log(f'  Public laws: {added:,} added')
    return added


def parse_bill(xml):
    """Support legacy/current XML layouts; ignore titles for bill portions."""
    bill = ET.fromstring(xml).find('bill')
    if bill is None:
        raise ValueError('Missing bill element')
    congress = bill.findtext('congress')
    kind = bill.findtext('type') or bill.findtext('billType')
    number = bill.findtext('number') or bill.findtext('billNumber')
    if not (congress and kind and number):
        raise ValueError('Missing bill identity')
    c, t, n = int(congress), kind.lower(), int(number)
    introduced = bill.findtext('introducedDate')
    if not introduced:
        raise ValueError(f'Missing introduction date: {c}/{t}/{n}')
    date.fromisoformat(introduced)
    titles = []
    for item in bill.findall('./titles/item'):
        kind = (item.findtext('titleType') or '').lower()
        title = item.findtext('title') or ''
        if kind.startswith('short title') and 'portion' not in kind and title:
            # Prefer the source's display title when it is an official short
            # title; otherwise prefer enacted, then the latest whole-bill title.
            rank = 2 if title == bill.findtext('title') else 1 if 'enacted' in kind else 0
            titles.append((rank, item.findtext('actionDate') or '', title))
    official = [item.findtext('title') for item in bill.findall('./titles/item')
                if (item.findtext('titleType') or '').lower().startswith('official title')]
    title = max(titles)[2] if titles else next((v for v in official if v), bill.findtext('title') or '')
    title = ' '.join(title.split())
    if len(title) > 160:
        title = title[:157].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    if not title:
        raise ValueError(f'Missing official title: {c}/{t}/{n}')
    if t not in BILL_TYPES:
        raise ValueError(f'Unknown bill type: {t}')
    return c, t, n, introduced, title


def _download(client, url, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Bill reference refresh time budget exhausted')
    chunks = []
    with client.stream('GET', url, timeout=min(10, remaining)) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline:
                raise TimeoutError('Bill reference refresh time budget exhausted')
            chunks.append(chunk)
    return b''.join(chunks)


def _fetch_group(client, congress, kind, existing, bootstrapped, deadline):
    base = f'{SOURCE}/{congress}/{kind}'
    rows = []
    if not bootstrapped:
        archive = _download(client, f'{base}/BILLSTATUS-{congress}-{kind}.zip', deadline)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            for name in zipped.namelist():
                if time.monotonic() >= deadline:
                    raise TimeoutError('Bill reference refresh time budget exhausted')
                if name.endswith('.xml'):
                    row = parse_bill(zipped.read(name))
                    if row[:2] != (congress, kind):
                        raise ValueError(f'Unexpected archive identity: {row[:3]}')
                    rows.append(row)
        if not rows:
            raise ValueError(f'Empty archive: {base}')
    else:
        listing = _download(client, base, deadline).decode('utf-8')
        numbers = {int(n) for n in re.findall(rf'BILLSTATUS-{congress}{kind}(\d+)\.xml', listing)}
        if not numbers:
            raise ValueError(f'No bill numbers in directory: {base}')
        for number in sorted(numbers - existing):
            row = parse_bill(_download(client, f'{base}/BILLSTATUS-{congress}{kind}{number}.xml', deadline))
            if row[:3] != (congress, kind, number):
                raise ValueError(f'Unexpected bill identity: {row[:3]}')
            rows.append(row)
    return rows


def refresh_reference(conn, max_seconds=100, current_congress=None, log=print):
    """Best effort, with a shared deadline and eight bounded network workers.

    Successful groups commit independently. A failed group leaves its prior
    data and sync marker intact and is retried next run. Historical markers
    mean a complete archive was parsed, not just a high-water bill number.
    """
    started = time.monotonic()
    deadline = started + max_seconds
    current = current_congress or (date.today().year - 1789) // 2 + 1
    conn.executescript(SCHEMA)
    synced = set(conn.execute('SELECT congress, type FROM bill_reference_sync'))
    existing = read_reference(conn)
    jobs = [(c, t) for c in range(116, current + 1) for t in BILL_TYPES
            if c == current or (c, t) not in synced]
    added = 0
    with httpx.Client(follow_redirects=True) as client, ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_group, client, c, t,
                        {n for rc, rt, n in existing if (rc, rt) == (c, t)},
                        (c, t) in synced, deadline): (c, t) for c, t in jobs
        }
        for future in as_completed(futures):
            c, t = futures[future]
            try:
                rows = future.result()
                # Introduction dates are immutable; daily refresh deliberately
                # leaves existing titles untouched (only new numbers fetched).
                conn.executemany('INSERT OR IGNORE INTO bill_reference VALUES (?, ?, ?, ?, ?)', rows)
                conn.execute('INSERT OR REPLACE INTO bill_reference_sync VALUES (?, ?, ?)',
                             (c, t, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                added += sum(row[:3] not in existing for row in rows)
                log(f'  Bill reference {c}/{t}: {len(rows)} fetched')
            except (httpx.HTTPError, TimeoutError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
                log(f'  Warning: bill reference {c}/{t}: {exc}')
        # Earlier Congresses are probed only until their laws are first held.
        held = {c for c, _ in read_public_laws(conn)}
        refresh_public_laws(conn, client, pool, [c for c in range(116, current + 1)
                                                 if c >= current - 1 or c not in held], deadline, log)
    load_reference.cache_clear()
    elapsed = time.monotonic() - started
    log(f'  Bill reference: {added:,} added in {elapsed:.1f}s')
    return {'added': added, 'elapsed_seconds': elapsed}
