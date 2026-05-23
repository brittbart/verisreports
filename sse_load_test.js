/**
 * k6 SSE load test for Verum Signal mobile API
 * 
 * Tests:
 *   1. SSE connections to /mobile/v1/debates/<slug>/stream
 *   2. Articles API under concurrent load
 *   3. Leaderboard API under concurrent load
 * 
 * Run:
 *   k6 run sse_load_test.js
 * 
 * Targets per Opus brief:
 *   - p95 latency < 500ms on all mobile API endpoints
 *   - SSE connection holds stable for 60+ minutes
 *   - No errors above 0.1% rate
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// ── Config ─────────────────────────────────────────────────────────────────

const BASE_URL = 'https://verumsignal.com/mobile/v1';
const SSE_SLUG = 'iowa-senate-dem-2026-r2'; // past debate — safe to hammer

// Custom metrics
const sseConnectErrors = new Counter('sse_connect_errors');
const sseEventsReceived = new Counter('sse_events_received');
const apiErrorRate = new Rate('api_errors');
const articleLatency = new Trend('article_latency', true);
const leaderboardLatency = new Trend('leaderboard_latency', true);
const sseFirstEventLatency = new Trend('sse_first_event_latency', true);

// ── Test scenarios ─────────────────────────────────────────────────────────

export const options = {
  scenarios: {
    // Scenario 1: Ramp up SSE connections
    sse_connections: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 },   // ramp to 10 concurrent SSE
        { duration: '60s', target: 50 },   // ramp to 50
        { duration: '60s', target: 100 },  // ramp to 100
        { duration: '60s', target: 50 },   // ramp down
        { duration: '30s', target: 0 },    // wind down
      ],
      exec: 'sseTest',
      gracefulRampDown: '10s',
    },

    // Scenario 2: Articles API under load (concurrent with SSE)
    articles_api: {
      executor: 'constant-vus',
      vus: 20,
      duration: '4m',
      exec: 'articlesTest',
      startTime: '30s', // start after SSE ramp begins
    },

    // Scenario 3: Leaderboard API
    leaderboard_api: {
      executor: 'constant-vus',
      vus: 10,
      duration: '4m',
      exec: 'leaderboardTest',
      startTime: '30s',
    },
  },

  thresholds: {
    // p95 latency < 500ms on REST endpoints
    'article_latency': ['p(95)<500'],
    'leaderboard_latency': ['p(95)<500'],
    // SSE first event received within 3 seconds
    'sse_first_event_latency': ['p(95)<3000'],
    // Error rate below 0.1%
    'api_errors': ['rate<0.001'],
    // HTTP errors
    'http_req_failed': ['rate<0.001'],
  },
};

// ── SSE test ───────────────────────────────────────────────────────────────

export function sseTest() {
  const url = `${BASE_URL}/debates/${SSE_SLUG}/stream?since_id=0`;
  const startTime = Date.now();

  const response = http.get(url, {
    headers: {
      'Accept': 'text/event-stream',
      'Cache-Control': 'no-cache',
    },
    timeout: '10s',
  });

  // Check connection succeeded
  const connected = check(response, {
    'SSE status 200': (r) => r.status === 200,
    'SSE content-type': (r) => r.headers['Content-Type'] &&
      r.headers['Content-Type'].includes('text/event-stream'),
    'SSE has body': (r) => r.body && r.body.length > 0,
  });

  if (!connected) {
    sseConnectErrors.add(1);
    apiErrorRate.add(1);
    return;
  }

  apiErrorRate.add(0);

  // Parse SSE events from response body
  const body = response.body;
  const events = body.split('\n\n').filter(e => e.trim());

  let firstEventTime = null;
  let connectedEventFound = false;

  for (const event of events) {
    const lines = event.split('\n');
    let eventType = '';
    let eventData = '';

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        eventData = line.slice(6).trim();
      }
    }

    if (eventType) {
      sseEventsReceived.add(1);

      if (eventType === 'connected' && !connectedEventFound) {
        connectedEventFound = true;
        firstEventTime = Date.now() - startTime;
        sseFirstEventLatency.add(firstEventTime);
      }
    }
  }

  check(null, {
    'SSE connected event received': () => connectedEventFound,
    'SSE events parsed': () => events.length > 0,
  });

  // Brief pause between SSE requests
  sleep(Math.random() * 2 + 1);
}

// ── Articles test ──────────────────────────────────────────────────────────

export function articlesTest() {
  const start = Date.now();

  const response = http.get(`${BASE_URL}/articles?limit=20&sort=recent`, {
    headers: { 'Accept': 'application/json' },
    timeout: '5s',
  });

  articleLatency.add(Date.now() - start);

  const ok = check(response, {
    'articles status 200': (r) => r.status === 200,
    'articles has data': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.status === 'ok' && body.data.articles.length > 0;
      } catch { return false; }
    },
  });

  apiErrorRate.add(ok ? 0 : 1);
  sleep(Math.random() * 2 + 1);
}

// ── Leaderboard test ───────────────────────────────────────────────────────

export function leaderboardTest() {
  const start = Date.now();

  const response = http.get(`${BASE_URL}/outlets/leaderboard?limit=50`, {
    headers: { 'Accept': 'application/json' },
    timeout: '5s',
  });

  leaderboardLatency.add(Date.now() - start);

  const ok = check(response, {
    'leaderboard status 200': (r) => r.status === 200,
    'leaderboard has outlets': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.status === 'ok' && body.data.outlets.length > 0;
      } catch { return false; }
    },
  });

  apiErrorRate.add(ok ? 0 : 1);
  sleep(Math.random() * 3 + 2);
}
