"""Offline integration tests for GovInfo parsing, incremental sync and outages."""
import io
import importlib.util
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bill_reference as ref


def xml(congress, kind, number, titles='', legacy=False):
    kind_tag = 'billType' if legacy else 'type'
    number_tag = 'billNumber' if legacy else 'number'
    return f'''<billStatus><bill><congress>{congress}</congress>
        <{kind_tag}>{kind.upper()}</{kind_tag}><{number_tag}>{number}</{number_tag}>
        <introducedDate>2025-01-03</introducedDate><title>Display</title>
        <titles>{titles}</titles></bill></billStatus>'''.encode()


def law_mods(congress, kind, number):
    return f'''<mods xmlns="http://www.loc.gov/mods/v3"><extension>
        <bill congress="{congress}" context="content" number="9" priority="reference" type="S"/>
        <bill congress="{congress}" context="legislative history" number="{number}" priority="primary" type="{kind}"/>
        </extension></mods>'''.encode()


def title(kind, value):
    return f'<item><titleType>{kind}</titleType><title>{value}</title></item>'


def main():
    titles = title('Short Title as Introduced for portions of this bill', 'Wrong portion')
    titles += title('Short Title as Introduced', 'Introduced')
    titles += title('Short Title as Enacted', 'Enacted')
    assert ref.parse_bill(xml(119, 'hr', 1, titles))[4] == 'Enacted'
    assert ref.parse_bill(xml(119, 'hr', 1, titles, legacy=True))[:3] == (119, 'hr', 1)
    official = 'To provide ' + 'a very long official title ' * 20
    trimmed = ref.parse_bill(xml(119, 'hr', 1, title('Official Title as Introduced', official)))[4]
    assert len(trimmed) <= 160 and trimmed.endswith('…')
    # Display title is used only if it is also a whole-bill short title.
    assert ref.parse_bill(xml(119, 'hr', 1, title('Short Title as Introduced', 'Display')))[4] == 'Display'

    conn = sqlite3.connect(':memory:')
    conn.executescript(ref.SCHEMA)
    requests = []
    listing = [1, 3]  # Deliberate hole: incremental checks must not use max().
    outage = False

    laws = {116: 2, 117: 0, 118: 0, 119: 17}  # published law counts per Congress
    law_requests = []

    def respond(request):
        path = request.url.path
        if path.startswith('/metadata/pkg/PLAW-'):
            law_requests.append(str(request.url))
            if outage:
                return httpx.Response(503, request=request)
            c, n = (int(v) for v in path.split('PLAW-')[1].split('/')[0].split('publ'))
            if n > laws.get(c, 0):
                return httpx.Response(302, headers={'Location': 'https://www.govinfo.gov/error'}, request=request)
            return httpx.Response(200, content=law_mods(c, 'HR', 100 + n), request=request)
        requests.append(str(request.url))
        if outage:
            return httpx.Response(503, request=request)
        c, kind = path.split('/')[3:5]
        if path.endswith('.zip'):
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w') as zipped:
                for n in listing:
                    zipped.writestr(f'BILLSTATUS-{c}{kind}{n}.xml', xml(c, kind, n))
            return httpx.Response(200, content=content.getvalue(), request=request)
        if path.endswith('.xml'):
            number = int(path.split(kind)[-1].split('.')[0])
            return httpx.Response(200, content=xml(c, kind, number), request=request)
        return httpx.Response(200, text=' '.join(f'BILLSTATUS-{c}{kind}{n}.xml' for n in listing), request=request)

    original_client = httpx.Client
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    with patch.object(ref.httpx, 'Client', client):
        first = ref.refresh_reference(conn, current_congress=119, log=lambda _: None)
        assert first['added'] == 4 * 8 * 2
        assert len(requests) == 32 and all(url.endswith('.zip') for url in requests)
        assert conn.execute('SELECT count(*) FROM bill_reference_sync').fetchone()[0] == 32
        # Public laws: probed upward until the first unpublished number;
        # the enacting bill is the primary legislative-history entry.
        assert ref.read_public_laws(conn) == {
            **{(116, n): (116, 'hr', 100 + n) for n in (1, 2)},
            **{(119, n): (119, 'hr', 100 + n) for n in range(1, 18)}}
        assert ref.parse_law_mods(law_mods(119, 'S', 5), 119) == ('s', 5)
        assert ref.parse_law_mods(law_mods(118, 'HR', 5), 119) is None
        law_requests.clear()
        laws[119] = 18
        ref.refresh_reference(conn, current_congress=119, log=lambda _: None)
        # Older Congresses already held aren't re-probed; 118 (current-1) and
        # empty 117 are; 119 resumes after its highest held law.
        assert not any('PLAW-116' in url for url in law_requests)
        assert (119, 18) in ref.read_public_laws(conn)
        assert any('PLAW-119publ18/' in url for url in law_requests)
        assert not any('PLAW-119publ17/' in url for url in law_requests)
        requests.clear()
        listing[:] = [1, 2, 3, 4]
        second = ref.refresh_reference(conn, current_congress=119, log=lambda _: None)
        assert second['added'] == 16
        assert len(requests) == 24
        assert all('/119/' in url and not url.endswith('.zip') for url in requests)
        assert sum(url.endswith('.xml') for url in requests) == 16
        assert not any(url.endswith('hr1.xml') or url.endswith('hr3.xml') for url in requests)
        requests.clear()
        prior = ref.read_reference(conn)
        markers = conn.execute('SELECT * FROM bill_reference_sync ORDER BY type, congress').fetchall()
        outage = True
        failed = ref.refresh_reference(conn, current_congress=119, log=lambda _: None)
        assert failed['added'] == 0 and len(requests) == 8
        assert ref.read_reference(conn) == prior
        assert len(ref.read_public_laws(conn)) == 20  # law outage keeps held laws
        assert conn.execute('SELECT * FROM bill_reference_sync ORDER BY type, congress').fetchall() == markers
        # Expired budget neither downloads nor changes durable reference data.
        requests.clear()
        ref.refresh_reference(conn, max_seconds=0, current_congress=119, log=lambda _: None)
        assert not requests and ref.read_reference(conn) == prior
        # A new Congress initializes once; former current Congress stays cached.
        outage = False
        requests.clear()
        rolled = ref.refresh_reference(conn, current_congress=120, log=lambda _: None)
        assert rolled['added'] == 32
        assert len(requests) == 8 and all('/120/' in url and url.endswith('.zip') for url in requests)
        # A failed first bootstrap never marks an empty group complete.
        fresh = sqlite3.connect(':memory:')
        outage = True
        ref.refresh_reference(fresh, current_congress=116, log=lambda _: None)
        assert fresh.execute('SELECT count(*) FROM bill_reference_sync').fetchone()[0] == 0
        outage = False
        recovered = ref.refresh_reference(fresh, current_congress=116, log=lambda _: None)
        assert recovered['added'] == 32

    spec = importlib.util.spec_from_file_location('refresh', Path(__file__).resolve().parent.parent / '07_refresh.py')
    refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh)
    exported = []
    with patch.object(refresh, 'init_db'), \
         patch.object(refresh.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reference', 110)) as run, \
         patch.object(refresh, '_load_module', return_value=SimpleNamespace(export_json=lambda: exported.append(True))):
        refresh.refresh(ingest_latest=False, rules_batch_size=0, verbose=False)
        assert exported == [True]  # Export proceeds even if the watchdog fires.
        assert run.call_args.kwargs['timeout'] == 110
        assert run.call_args.args[0][-2:] == ['--max-seconds', '100']
    print('Bill reference tests passed: XML titles/legacy schema, bootstrap, holes/new numbers, outage, deadline, rollover, public laws.')


if __name__ == '__main__':
    main()
