import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate } from 'k6/metrics';

const base = 'http://app:8080/api';
const mode = __ENV.MODE || 'mixed';
const rate = Number(__ENV.RATE || 1);
const seconds = Number(__ENV.SECONDS_PER_STEP || 30);
const vus = Number(__ENV.VUS || Math.max(20, rate * 8));
const fixtures = mode === 'heavy' ? null : JSON.parse(open('/run-data/fixtures.json'));
const phase = { phase: 'load' };
const operationErrors = new Rate('operation_errors');
const loadWindowStart = new Counter('load_window_start');
const expectedConflict = http.expectedStatuses(201, 409);
const ok = http.expectedStatuses(200);
const sessionName = 'sergey_rodionov_session';
const dates = 'date_from=2026-01-01&date_to=2026-12-31';

const profile = [
    ['free_slots', 28],
    ['appointments_list', 25],
    ['appointment_detail', 15],
    ['specialists', 10],
    ['appointment_create', 9],
    ['services', 8],
    ['appointment_cancel', 4],
    ['summary', 1],
];
const operations = profile.flatMap(([name, weight]) => Array(weight).fill(name));

if (!['smoke', 'mixed', 'heavy'].includes(mode) ||
    ![rate, seconds, vus].every(value => Number.isSafeInteger(value) && value > 0)) {
    throw new Error('Use MODE=smoke, mixed or heavy and positive integers for RATE, SECONDS_PER_STEP and VUS');
}
if (fixtures && (!Array.isArray(fixtures.slots) || !Array.isArray(fixtures.bookings) ||
    Math.min(fixtures.slots.length, fixtures.bookings.length) < (mode === 'smoke' ? 9 : rate * seconds))) {
    throw new Error('Not enough prepared IDs for the requested stage');
}

export const options = {
    scenarios: {
        [mode]: mode === 'smoke'
            ? { executor: 'shared-iterations', vus: 1, iterations: 9, maxDuration: '2m' }
            : {
                executor: 'constant-arrival-rate', rate, timeUnit: '1s',
                duration: `${seconds}s`, preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '35s',
            },
    },
    summaryTrendStats: ['count', 'med', 'p(95)', 'max'],
    thresholds: {
        'http_req_duration{phase:load}': [mode === 'smoke' ? 'max>=0' : 'p(95)<500'],
        'http_req_failed{phase:load}': [mode === 'smoke' ? 'rate==0' : 'rate<0.01'],
        operation_errors: [mode === 'smoke' ? 'rate==0' : 'rate<0.01'],
    },
};

export function setup() {
    const response = http.post(`${base}/auth/login`, JSON.stringify({ login: 'demo', password: 'demo' }), {
        headers: { 'Content-Type': 'application/json' },
        redirects: 0,
        tags: { phase: 'setup', operation: 'login' },
    });
    const cookies = response.cookies[sessionName];
    if (response.status !== 200 || !cookies || !cookies[0].value) {
        throw new Error(`Login failed, HTTP ${response.status}`);
    }
    console.log(`sergey_rodionov | WORKING | ${mode} | ${rate} req/s | ${seconds}s | VUs ${mode === 'smoke' ? 1 : vus}`);
    return { cookie: cookies[0].value };
}

function responseBody(response) {
    try {
        return response.json();
    } catch (_) {
        return null;
    }
}

function request(method, path, operation, session, data, callback = ok) {
    return http.request(method, base + path, data === null ? null : JSON.stringify(data), {
        cookies: { [sessionName]: { value: session, replace: true } },
        headers: data === null ? {} : { 'Content-Type': 'application/json' },
        tags: { ...phase, operation, name: operation },
        responseCallback: callback,
        redirects: 0,
        timeout: '30s',
    });
}

function booking(slot) {
    return { slot_id: slot, client_name: 'Клиент ДЗ2', client_contact: 'dz2@example.test' };
}

export default function ({ cookie }) {
    const i = exec.scenario.iterationInTest;
    if (i === 0) loadWindowStart.add(1, phase);
    // The first 100 starts and every following block have the exact table weights.
    // A coprime stride interleaves operations within each block.
    const operation = mode === 'heavy' ? 'summary' : mode === 'smoke'
        ? (i < profile.length ? profile[i][0] : 'appointment_conflict')
        : operations[(37 * i + 59) % 100];
    const get = (path) => request('GET', path, operation, cookie, null);
    let response;
    let valid;
    if (operation === 'free_slots' || operation === 'appointments_list') {
        response = get(operation === 'free_slots'
            ? `/slots?service_id=1&${dates}&page=1&size=20`
            : '/appointments?page=1&size=20');
        const body = responseBody(response);
        valid = response.status === 200 && body && Array.isArray(body.items) &&
            Number.isInteger(body.total) && body.page === 1 && body.size === 20;
    } else if (operation === 'services' || operation === 'specialists') {
        response = get(`/${operation}`);
        valid = response.status === 200 && Array.isArray(responseBody(response));
    } else if (operation === 'appointment_detail') {
        const id = fixtures.bookings[i];
        response = get(`/appointments/${id}`);
        const body = responseBody(response);
        valid = response.status === 200 && body && body.id === id;
    } else if (operation === 'appointment_cancel') {
        const id = fixtures.bookings[i];
        response = request('POST', `/appointments/${id}/cancel`, operation, cookie, null);
        const body = responseBody(response);
        valid = response.status === 200 && body && body.id === id && body.status === 'cancelled' && !!body.cancelled_at;
    } else if (operation === 'appointment_create' || operation === 'appointment_conflict') {
        const conflict = operation === 'appointment_conflict';
        const slot = conflict ? fixtures.occupied_slot : fixtures.slots[i];
        response = request('POST', '/appointments', operation, cookie, booking(slot), expectedConflict);
        const body = responseBody(response);
        const rejected = response.status === 409 && body &&
            body.detail === 'Слот занят или пересекается с действующей записью';
        const inserted = response.status === 201 && body && body.slot &&
            body.slot.id === slot && body.status === 'booked';
        valid = conflict ? rejected : inserted || (mode === 'mixed' && rejected);
    } else {
        response = get(`/summary?${dates}`);
        const body = responseBody(response);
        valid = response.status === 200 && body &&
            Array.isArray(body.specialists) && Number.isInteger(body.total_appointments);
    }
    const correct = check(response, { [operation]: () => !!valid }, phase);
    operationErrors.add(!correct, { ...phase, operation });
    if (!correct) {
        console.error(`${operation}: unexpected HTTP ${response.status}; error_code=${response.error_code || 0}; iteration=${i}`);
    }
}
