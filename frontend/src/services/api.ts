const BASE = 'http://localhost:8000';

export interface Track {
  index: number;
  name: string;
  color: string;
  volume: number;
  muted: boolean;
  solo: boolean;
  type: string;
}

export interface ProjectStatus {
  tempo: number;
  time_sig: string;
  track_count: number;
  tracks: Track[];
  bridge_connected: boolean;
  playing: boolean;
  recording: boolean;
}

async function post(path: string, body?: object) {
  return fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

export async function getStatus(): Promise<ProjectStatus> {
  const res = await fetch(`${BASE}/status`);
  return res.json();
}

export async function play() {
  await post('/play');
}

export async function stop() {
  await post('/stop');
}

export async function record() {
  await post('/record');
}

export async function addTrack(type: string, name?: string) {
  await post('/track', { type, name: name || type });
}

export async function undo() {
  await post('/undo');
}
