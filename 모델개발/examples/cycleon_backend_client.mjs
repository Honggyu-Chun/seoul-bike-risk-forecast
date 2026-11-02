// Server-side Node 18+ example. Do not import into a browser bundle.
const base = process.env.CYCLEON_AI_BASE_URL ?? 'http://127.0.0.1:18011';
const token = process.env.CYCLEON_SERVICE_TOKEN;
if (!token || token.length < 32) throw new Error('Set the server-side CYCLEON_SERVICE_TOKEN');
const ids = (process.env.CYCLEON_STATION_IDS ?? 'ST-MOCK-1,ST-UNKNOWN').split(',');

async function call(path, body) {
  const response = await fetch(`${base.replace(/\/$/, '')}${path}`, {
    method: body ? 'POST' : 'GET',
    headers: {Authorization: `Bearer ${token}`, ...(body ? {'Content-Type': 'application/json'} : {})},
    ...(body ? {body: JSON.stringify(body)} : {}),
    signal: AbortSignal.timeout(10000), // Client example, not an achieved latency SLA.
  });
  if (!response.ok) throw new Error(`CycleOn HTTP ${response.status}`);
  return response.json(); // Preserve item-level null, reason, timestamps and model identifiers.
}
try {
  const status = await call('/v1/status');
  const predictions = await call('/v1/predictions', {station_ids: ids, horizons_hours: [1,2,3]});
  const recommendations = await call('/v1/recommendations', {
    latitude: 37.5665, longitude: 126.978, horizon_hours: 1, radius_m: 500,
  });
  console.log(JSON.stringify({status, predictions, recommendations}, null, 2));
} catch {
  console.error('CycleOn call failed. Check status, connection and server configuration.');
  process.exitCode = 1;
}
