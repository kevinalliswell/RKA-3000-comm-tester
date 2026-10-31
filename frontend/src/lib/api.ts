import { invoke, isTauri } from '@tauri-apps/api/core';

export type DeviceType = 'taie' | 'azbil' | 'balance';
export type Parity = 'N' | 'E' | 'O';

export interface ScanConfig {
  device: DeviceType;
  port: string;
  baudrate: number;
  parity: Parity;
  stopbits: number;
  timeout_s: number;
  start_address: number;
  end_address: number;
  balance_protocol: 'gs_cpa' | 'longteng';
  balance_lines: number;
  balance_start_output: boolean;
}

export interface PortInfo {
  device: string;
  description: string;
  hwid: string;
}

export interface ScanResult {
  device: DeviceType;
  port: string;
  address: number | null;
  status: string;
  values: Record<string, unknown>;
  detail: string;
  timestamp: string;
}

export interface ScanJob {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'error';
  config: ScanConfig;
  results: ScanResult[];
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (isTauri()) {
    return invoke<T>('api_request', {
      method: init?.method ?? 'GET', path,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
  }
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    signal: AbortSignal.timeout(10000),
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error ?? `请求失败（${response.status}）`);
  }
  return payload;
}

export function health(): Promise<{ ok: boolean; service: string; running_scans: number; log_path: string }> {
  return request('/api/health');
}

export async function ports(): Promise<PortInfo[]> {
  const response = await request<{ ports: PortInfo[] }>('/api/ports');
  return response.ports;
}

export function startScan(config: ScanConfig): Promise<ScanJob> {
  return request('/api/scans', { method: 'POST', body: JSON.stringify(config) });
}

export function getScan(id: string): Promise<ScanJob> {
  return request(`/api/scans/${encodeURIComponent(id)}`);
}

export function cancelScan(id: string): Promise<ScanJob> {
  return request(`/api/scans/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: '{}' });
}

export async function loadProfile(): Promise<ScanConfig | null> {
  return (await request<{ config: ScanConfig | null }>('/api/config')).config;
}

export async function saveProfile(config: ScanConfig): Promise<ScanConfig> {
  return (await request<{ config: ScanConfig }>('/api/config', {
    method: 'POST', body: JSON.stringify(config),
  })).config;
}

export function exportLog(): Promise<{ filename: string; content: string }> {
  return request('/api/log');
}
