const corsHeaders = (origin, allowed) => ({
  'Access-Control-Allow-Origin': allowed || origin || '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Vary': 'Origin'
});

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...headers }
  });
}

function validMonth(value) {
  if (!/^\d{4}-\d{2}$/.test(value || '')) return false;
  const month = Number(value.slice(5, 7));
  return month >= 1 && month <= 12;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = corsHeaders(request.headers.get('Origin'), env.ALLOWED_ORIGIN);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return json({ error: 'Method not allowed' }, 405, cors);
    }

    if (url.pathname === '/api/health') {
      return json({ ok: true, service: 'forex-history', symbol: 'EURUSD', timeframe: 'M1' }, 200, cors);
    }

    if (url.pathname === '/api/months') {
      const from = url.searchParams.get('from');
      const to = url.searchParams.get('to');
      if (!/^\d{4}-\d{2}$/.test(from || '') || !validMonth(to)) {
        return json({ error: 'Use from=YYYY-MM&to=YYYY-MM' }, 400, cors);
      }
      const prefix = 'EURUSD/M1/';
      const listed = await env.MARKET_DATA.list({ prefix });
      const months = listed.objects
        .map(o => o.key.slice(prefix.length, prefix.length + 7))
        .filter(m => validMonth(m) && m >= from && m <= to)
        .sort();
      return json({ symbol: 'EURUSD', timeframe: 'M1', months, truncated: listed.truncated }, 200, cors);
    }

    if (url.pathname === '/api/candles') {
      const month = url.searchParams.get('month');
      if (!validMonth(month)) {
        return json({ error: 'Use month=YYYY-MM' }, 400, cors);
      }
      const key = `EURUSD/M1/${month}.csv.gz`;
      const object = await env.MARKET_DATA.get(key);
      if (!object) return json({ error: 'Month not available', month, key }, 404, cors);
      const headers = new Headers(cors);
      headers.set('Content-Type', 'text/csv; charset=utf-8');
      headers.set('Content-Encoding', 'gzip');
      headers.set('Cache-Control', 'public, max-age=86400, s-maxage=604800');
      headers.set('ETag', object.httpEtag);
      headers.set('X-Data-Month', month);
      headers.set('X-Data-Source', 'Dukascopy');
      if (object.size) headers.set('Content-Length', String(object.size));
      return new Response(request.method === 'HEAD' ? null : object.body, { headers });
    }

    return json({
      service: 'Forex Research historical data API',
      endpoints: ['/api/health', '/api/months?from=YYYY-MM&to=YYYY-MM', '/api/candles?month=YYYY-MM']
    }, 200, cors);
  }
};
