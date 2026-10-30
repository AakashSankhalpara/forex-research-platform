#!/usr/bin/env python3
"""Convert Dukascopy downloader BID/ASK M1 parquet files into monthly gzip CSVs and upload to Cloudflare R2.

Expected input tree (the downloader may add category folders above EUR-USD):
  data/m1/**/EUR-USD/BID/YYYY-MM.parquet
  data/m1/**/EUR-USD/ASK/YYYY-MM.parquet

CSV schema:
  time,bid_open,bid_high,bid_low,bid_close,ask_open,ask_high,ask_low,ask_close,bid_volume,ask_volume
Times are Unix seconds (UTC). Keep bid and ask separate for realistic spread-aware research.
"""
import argparse
import gzip
import io
import os
from pathlib import Path

import boto3
import pandas as pd


def find_side_files(root: Path, side: str):
    return sorted(p for p in root.rglob('*.parquet') if side in p.parts and 'EUR-USD' in p.parts)


def read_side(files, side):
    frames = []
    for path in files:
        df = pd.read_parquet(path)
        if 'time' not in df.columns:
            raise ValueError(f"Missing 'time' column in {path}; inspect the installed downloader's parquet schema.")
        df['time'] = pd.to_datetime(df['time'], utc=True)
        rename = {c: f'{side.lower()}_{c}' for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns}
        df = df.rename(columns=rename)
        frames.append(df[['time', *rename.values()]])
    if not frames:
        return pd.DataFrame(columns=['time'])
    return pd.concat(frames, ignore_index=True).drop_duplicates('time').sort_values('time')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='data', help='Dukascopy downloader data directory')
    parser.add_argument('--dry-run', action='store_true', help='Build and validate files without uploading')
    parser.add_argument('--from-month', help='Optional inclusive YYYY-MM filter')
    parser.add_argument('--to-month', help='Optional inclusive YYYY-MM filter')
    args = parser.parse_args()

    root = Path(args.data_dir)
    bid_files = find_side_files(root, 'BID')
    ask_files = find_side_files(root, 'ASK')
    if not bid_files or not ask_files:
        raise SystemExit(
            'Could not find both BID and ASK EUR-USD parquet files. Download both sides first; '
            'then inspect the data folder layout and adjust find_side_files if needed.'
        )

    bid = read_side(bid_files, 'bid')
    ask = read_side(ask_files, 'ask')
    merged = bid.merge(ask, on='time', how='inner').dropna()
    if merged.empty:
        raise SystemExit('No matching BID/ASK timestamps found.')

    merged['month'] = merged['time'].dt.strftime('%Y-%m')
    if args.from_month:
        merged = merged[merged['month'] >= args.from_month]
    if args.to_month:
        merged = merged[merged['month'] <= args.to_month]

    required = ['bid_open','bid_high','bid_low','bid_close','ask_open','ask_high','ask_low','ask_close']
    missing = [c for c in required if c not in merged.columns]
    if missing:
        raise SystemExit(f'Missing expected OHLC columns: {missing}. Inspect parquet schema.')

    session = None
    if not args.dry_run:
        account_id = os.environ['R2_ACCOUNT_ID']
        access_key = os.environ['R2_ACCESS_KEY_ID']
        secret_key = os.environ['R2_SECRET_ACCESS_KEY']
        bucket_name = os.environ.get('R2_BUCKET', 'forex-market-data')
        session = boto3.client(
            's3',
            endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name='auto'
        )

    columns = ['time', *required]
    for col in ['bid_volume', 'ask_volume']:
        if col in merged.columns:
            columns.append(col)

    for month, group in merged.groupby('month', sort=True):
        group = group.sort_values('time').copy()
        group['time'] = (group['time'].astype('int64') // 10**9).astype('int64')
        csv_bytes = group[columns].to_csv(index=False, float_format='%.5f').encode('utf-8')
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=6, mtime=0) as gz:
            gz.write(csv_bytes)
        payload = compressed.getvalue()
        key = f'EURUSD/M1/{month}.csv.gz'
        print(f'{month}: {len(group):,} rows, {len(payload):,} compressed bytes -> {key}')
        if session:
            session.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=payload,
                ContentType='text/csv; charset=utf-8',
                ContentEncoding='gzip',
                CacheControl='public, max-age=86400'
            )

    print('Completed. Validate /api/months and /api/candles after deploying the Worker.')


if __name__ == '__main__':
    main()
