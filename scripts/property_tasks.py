"""Shared JSON interfaces for individual property-data tasks; no workflow or Notion access."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import property_sources as data
from housing_developments import resolve_origin

TASKS = {
    'property_facts': 'City property, lot and zoning facts, numeric floor-area estimates and corner geometry.',
    'sales_history': 'Recorded ACRIS deeds and the latest deed’s buyer/seller parties.',
    'violations': 'DOB, ECB and HPD violation records only.',
    'permits': 'BIS and DOB NOW job filings only; a filing is not proof of an issued permit.',
    'flood': 'Effective and preliminary flood-map results only.',
    'comps': 'Candidate closed sales and DOB alteration signals on the first eight candidates.',
}


def lot_parts(p):
    return data.BORO_CODE[p['borough']], str(int(p['block'])), str(int(p['lot']))


def numeric_facts(p):
    values = {key: data.fnum(p.get(key)) for key in ('lotarea', 'residfar', 'bldgarea')}
    area, far, built = (values[key] for key in ('lotarea', 'residfar', 'bldgarea'))
    maximum = area * far if area is not None and far is not None else None
    return {**values, 'max_zfa': maximum,
            'unused_sf': maximum - built if maximum is not None and built is not None else None}


class Result:
    def __init__(self, task, p, origin):
        self.successes = 0
        self.value = {
            'schema_version': '1.0', 'task': task,
            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'checked': dt.date.today().isoformat(), 'status': 'complete',
            'property': {'bbl': p['bbl'], 'address': p.get('address'), 'borough': p.get('borough'),
                         'lat': data.fnum(p.get('latitude')), 'lon': data.fnum(p.get('longitude'))},
            'sources': {'pluto': origin['source_url']}, 'errors': [],
        }

    def capture(self, name, callback, url):
        self.value['sources'][name] = url
        data.say(name)
        try:
            result = callback()
            self.successes += 1
            return result
        except Exception as exc:
            self.value['errors'].append({'source': name, 'message': f'{type(exc).__name__}: {exc}'})
            return None

    def finish(self):
        if self.value['errors']:
            self.value['status'] = 'partial' if self.successes else 'failed'
        return self.value


def collect(task, p, origin, *, radius=0.5, months=18, min_amt=100000):
    """Run just the requested task against a previously resolved lot."""
    if task not in TASKS:
        raise ValueError('Unknown property task')
    result = Result(task, p, origin)
    output = result.value
    boro, block, lot = lot_parts(p)
    bbl = p['bbl']
    source_url = lambda key: 'https://data.cityofnewyork.us/d/' + data.DS[key]
    if task == 'property_facts':
        # Resolution already fetched this task's primary source successfully.
        result.successes += 1
        output.update({'pluto': p, 'numeric': numeric_facts(p)})
        output['corner'] = result.capture('corner', lambda: data.near_corner_estimate(p), source_url('pluto'))
    elif task == 'sales_history':
        output['deeds'] = result.capture('deeds', lambda: sorted(
            data.acris_deeds(boro, [(block, lot)], None), key=lambda row: row['date'], reverse=True), source_url('master'))
        output['sources']['deed_lots'] = source_url('legals')
        output['parties_document_id'] = None
        output['parties'] = None
        if output['deeds']:
            document = output['deeds'][0]['document_id']
            output['parties_document_id'] = document
            output['parties'] = result.capture('parties', lambda: data.acris_parties(document), source_url('parties'))
        elif output['deeds'] == []:
            output['parties'] = {'buyer': [], 'seller': []}
    elif task in ('violations', 'permits'):
        if task == 'violations':
            queries = [('dob', 'dobviol', lambda: data.dob_violation_rows(boro, block, lot)),
                       ('ecb', 'ecb', lambda: data.ecb_violation_rows(boro, block, lot)),
                       ('hpd', 'hpd', lambda: data.hpd_violation_rows(bbl))]
        else:
            queries = [('bis', 'bisjobs', lambda: data.bis_job_rows(bbl)),
                       ('dob_now', 'dobnow', lambda: data.dobnow_job_rows(bbl))]
        output[task] = {name: result.capture(name, query, source_url(key)) for name, key, query in queries}
        output['counts'] = {name: len(rows) if rows is not None else None for name, rows in output[task].items()}
    elif task == 'flood':
        lat, lon = data.fnum(p.get('latitude')), data.fnum(p.get('longitude'))
        if lat is None or lon is None or not all(math.isfinite(v) for v in (lat, lon)):
            raise ValueError('Flood checks require known property coordinates')
        output['flood'] = result.capture('flood', lambda: data.flood_zone(lat, lon), data.FEMA_NFHL)
        output['sources']['preliminary_flood'] = data.NYC_PFIRM
        if output['flood'] is not None:
            flood = output['flood']
            result.successes = 0
            for key, ok, fields in [('effective_flood', 'effective_ok', ('zone', 'subtype', 'sfha', 'bfe')),
                                    ('preliminary_flood', 'preliminary_ok', ('pzone', 'psfha', 'pbfe'))]:
                if flood.get(ok):
                    result.successes += 1
                else:
                    for field in fields:
                        flood[field] = None
                    output['errors'].append({'source': key, 'message': 'Flood-map service could not be checked'})
    else:
        output['options'] = {'radius': radius, 'months': months, 'min_amt': min_amt}
        output['candidate_comps'] = result.capture('candidate_comps', lambda: data.find_comps(p, radius, months, min_amt), source_url('master'))
        output['sources'].update({'deed_lots': source_url('legals'), 'bis_jobs': source_url('bisjobs'), 'dob_now_jobs': source_url('dobnow')})
        if output['candidate_comps']:
            result.capture('renovation_signals', lambda: data.renovation_signal(output['candidate_comps'][:8]), source_url('bisjobs'))
    return result.finish()


def main(task, argv=None):
    parser = argparse.ArgumentParser(description=TASKS[task])
    parser.add_argument('address', nargs='?')
    parser.add_argument('--borough', choices=list(data.BORO_CODE))
    parser.add_argument('--bbl')
    parser.add_argument('--out', type=Path, help='also save the same JSON printed to stdout')
    if task == 'comps':
        parser.add_argument('--radius', type=float, default=0.5, help='miles')
        parser.add_argument('--months', type=int, default=18)
        parser.add_argument('--min-amt', type=float, default=100000)
    args = parser.parse_args(argv)
    if bool(args.address) == bool(args.bbl):
        parser.error('Provide one address with --borough, or --bbl')
    if args.address and not args.borough:
        parser.error('An address requires --borough')
    if args.bbl and not re.fullmatch(r'[1-5][0-9]{9}', args.bbl):
        parser.error('--bbl must be ten digits beginning with borough code 1–5')
    if task == 'comps' and (not math.isfinite(args.radius) or not 0 < args.radius <= 25
                            or not 1 <= args.months <= 120 or not math.isfinite(args.min_amt) or args.min_amt < 0):
        parser.error('Use a radius greater than 0 and at most 25 miles, months 1–120, and a finite nonnegative minimum amount')
    try:
        _, _, origin = resolve_origin(data.SESSION, args.address, args.borough, args.bbl)
        p = data.pluto_by_bbl(origin['bbl'])
        if not p:
            raise ValueError('The resolved PLUTO lot is no longer available')
        p['bbl'] = data.norm_bbl(p['bbl'])
        options = {key: getattr(args, key) for key in ('radius', 'months', 'min_amt')} if task == 'comps' else {}
        output = collect(task, p, origin, **options)
    except Exception as exc:
        output = {'schema_version': '1.0', 'task': task, 'status': 'failed',
                  'errors': [{'source': 'property', 'message': f'{type(exc).__name__}: {exc}'}]}
    serialized = json.dumps(output, indent=2, allow_nan=False) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding='utf-8')
    sys.stdout.write(serialized)
    return {'complete': 0, 'partial': 1, 'failed': 2}[output['status']]
