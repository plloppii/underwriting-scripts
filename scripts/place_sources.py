"""Independent restaurant, subway-station and park searches over official datasets."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
import requests
from housing_developments import SourceError, bbox, distance_mi, nearest_point, resolve_origin
from property_sources import SESSION

SOURCES = {
    'restaurants': {'host': 'https://data.cityofnewyork.us', 'id': '43nn-pn8j', 'name': 'DOHMH restaurant inspection records',
        'select': 'camis,dba,boro,building,street,cuisine_description,latitude,longitude,inspection_date',
        'order': 'camis,inspection_date DESC,:id',
        'coverage': 'Inspection-record business directory, deduplicated by CAMIS. Inclusion is not proof a business is currently open; no Google ratings.'},
    'train_stations': {'host': 'https://data.ny.gov', 'id': '39hk-dx4f', 'name': 'MTA subway stations',
        'select': 'gtfs_stop_id,stop_name,borough,daytime_routes,gtfs_latitude,gtfs_longitude,complex_id,ada',
        'order': 'gtfs_stop_id', 'coverage': 'MTA subway station records by GTFS stop ID; not commuter rail, service status, or station entrances.'},
    'parks': {'host': 'https://data.cityofnewyork.us', 'id': 'enfh-gkve', 'name': 'NYC Parks properties',
        'select': 'gispropnum,signname,borough,address,acres,typecategory,multipolygon,retired',
        'order': 'gispropnum', 'coverage': 'Published NYC Parks properties. Straight-line polygon distance is not a walking route or verified entrance.'},
}


def borough_name(value):
    value = str(value or '').strip().upper()
    for name, aliases in [('Manhattan', {'M', 'MN', 'MANHATTAN'}), ('Bronx', {'X', 'BX', 'BRONX'}),
                          ('Brooklyn', {'B', 'BK', 'BROOKLYN'}), ('Queens', {'Q', 'QN', 'QUEENS'}),
                          ('Staten Island', {'R', 'SI', 'STATEN ISLAND'})]:
        if value in aliases:
            return name
    return None


def fetch(session, url, params=None):
    from housing_developments import get_json
    # Reuse bounded retries while fixing the destination to one of the two official hosts.
    if url.startswith('https://data.cityofnewyork.us/'):
        return get_json(session, url[len('https://data.cityofnewyork.us'):], params)
    if not url.startswith('https://data.ny.gov/'):
        raise SourceError('Unsupported data host')
    import time
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(attempt + 1)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                raise SourceError('MTA source request failed') from exc
            time.sleep(attempt + 1)


def read_rows(session, spec, where, page_size=1000):
    base = spec['host']
    url = base + '/resource/' + spec['id'] + '.json'
    meta = fetch(session, base + '/api/views/' + spec['id'] + '.json')
    if not isinstance(meta, dict) or meta.get('rowsUpdatedAt') is None:
        raise SourceError('Missing dataset metadata')
    counts = fetch(session, url, {'$where': where, '$select': 'count(*) AS n'})
    expected = int(counts[0]['n'])
    rows = []
    while len(rows) < expected:
        page = fetch(session, url, {'$select': spec['select'], '$where': where, '$order': spec['order'],
                                   '$limit': page_size, '$offset': len(rows)})
        if not isinstance(page, list) or not page:
            raise SourceError('Incomplete source pagination')
        rows.extend(page)
    after = fetch(session, base + '/api/views/' + spec['id'] + '.json')
    if len(rows) != expected or meta['rowsUpdatedAt'] != after.get('rowsUpdatedAt'):
        raise SourceError('Dataset changed or returned an inconsistent row count; retry')
    return rows, dt.datetime.fromtimestamp(meta['rowsUpdatedAt'], dt.timezone.utc).isoformat()


def collect(kind, lat, lon, radius, session=None):
    if kind not in SOURCES:
        raise ValueError('Unknown place category')
    if not all(math.isfinite(v) for v in (lat, lon, radius)) or not (40 <= lat <= 42 and -75 <= lon <= -72 and 0 < radius <= 25):
        raise ValueError('Use NYC-area coordinates and a radius greater than zero and at most 25 miles')
    spec = SOURCES[kind]
    south, north, west, east = bbox(lat, lon, radius)
    if kind == 'parks':
        where = f"intersects(multipolygon, 'POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))') AND retired=false"
    else:
        a, b = ('gtfs_latitude', 'gtfs_longitude') if kind == 'train_stations' else ('latitude', 'longitude')
        where = f'{a} between {south} and {north} AND {b} between {west} and {east}'
    result = {'schema_version': '1.0', 'status': 'complete', 'category': kind,
              'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'query': {'lat': lat, 'lon': lon, 'radius_mi': radius},
              'source': {'dataset_id': spec['id'], 'url': spec['host'] + '/d/' + spec['id'],
                         'name': spec['name'], 'coverage': spec['coverage']}, 'records': [], 'errors': []}
    try:
        rows, updated = read_rows(session or SESSION, spec, where)
        records = {}
        for row in rows:
            if kind == 'parks':
                point = nearest_point(row['multipolygon'], lat, lon)
                identifier, name, record_type = 'nyc-parks:' + row['gispropnum'], row.get('signname'), 'Park'
                extra = {'geometry': row['multipolygon'], 'acres': float(row['acres']) if row.get('acres') is not None else None,
                         'park_type': row.get('typecategory'), 'address': row.get('address')}
                coordinate_basis = 'nearest_polygon_point'
                params = {'gispropnum': row['gispropnum']}
            elif kind == 'train_stations':
                point = float(row['gtfs_latitude']), float(row['gtfs_longitude'])
                identifier, name, record_type = 'mta:' + row['gtfs_stop_id'], row.get('stop_name'), 'Subway station'
                extra = {'routes': row.get('daytime_routes'), 'complex_id': row.get('complex_id'), 'ada_source_value': row.get('ada')}
                coordinate_basis = 'published_station_coordinate'
                params = {'gtfs_stop_id': row['gtfs_stop_id']}
            else:
                point = float(row['latitude']), float(row['longitude'])
                identifier, name, record_type = 'nyc-dohmh:' + row['camis'], row.get('dba'), 'Restaurant'
                extra = {'address': ' '.join(str(row[k]) for k in ('building', 'street') if row.get(k)) or None,
                         'cuisine': row.get('cuisine_description'), 'inspection_date': row.get('inspection_date')}
                coordinate_basis = 'published_business_coordinate'
                params = {'camis': row['camis']}
            distance = distance_mi(lat, lon, *point)
            if distance > radius or not name:
                continue
            from urllib.parse import urlencode
            record = {'source_id': identifier, 'name': name, 'type': record_type,
                      'lat': point[0], 'lon': point[1], 'location_basis': coordinate_basis,
                      'distance_mi': round(distance, 6), 'distance_method': 'straight_line',
                      'borough': borough_name(row.get('borough', row.get('boro'))),
                      'source_url': spec['host'] + '/resource/' + spec['id'] + '.json?' + urlencode(params), **extra}
            # Restaurants are ordered newest inspection first; keep one record per business.
            if identifier in records and kind != 'restaurants':
                raise SourceError('Duplicate place identifier; inspect the source before collapsing records')
            records.setdefault(identifier, record)
        result['records'] = sorted(records.values(), key=lambda r: (r['distance_mi'], r['source_id']))
        result['source'].update({'data_updated_at': updated, 'candidate_count': len(rows)})
    except (SourceError, KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        result.update({'status': 'failed', 'records': [], 'errors': [{'source': kind, 'message': str(exc)}]})
    return result


def main(kind, argv=None):
    parser = argparse.ArgumentParser(description='Return nearby ' + kind.replace('_', ' ') + ' as independent JSON.')
    parser.add_argument('address', nargs='?')
    parser.add_argument('--borough', choices=['MN', 'BX', 'BK', 'QN', 'SI'])
    parser.add_argument('--bbl')
    parser.add_argument('--lat', type=float)
    parser.add_argument('--lon', type=float)
    parser.add_argument('--radius-mi', type=float, default=0.5)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    coords = args.lat is not None or args.lon is not None
    if sum((bool(args.address), bool(args.bbl), coords)) != 1 or coords and (args.lat is None or args.lon is None):
        parser.error('Provide one address/borough, --bbl, or both --lat and --lon')
    if not math.isfinite(args.radius_mi) or not 0 < args.radius_mi <= 25:
        parser.error('--radius-mi must be greater than 0 and at most 25')
    try:
        lat, lon = args.lat, args.lon
        origin = None
        if not coords:
            lat, lon, origin = resolve_origin(SESSION, args.address, args.borough, args.bbl)
        result = collect(kind, lat, lon, args.radius_mi)
        if origin:
            result['query']['property'] = origin
    except (SourceError, ValueError) as exc:
        result = {'schema_version': '1.0', 'status': 'failed', 'records': [], 'errors': [{'source': 'query', 'message': str(exc)}]}
    serialized = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding='utf-8')
    sys.stdout.write(serialized)
    return 0 if result['status'] == 'complete' else 2
