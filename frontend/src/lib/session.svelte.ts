/** Presentation state: task lifecycle and polling. Serial rules stay in Python. */
import * as api from './api';
import type { DeviceType, PortInfo, ScanConfig, ScanJob } from './api';

export class ScanSession {
  config: ScanConfig = $state({
    device: 'taie', port: '', baudrate: 19200, parity: 'N', stopbits: 1,
    timeout_s: 0.5, start_address: 1, end_address: 6, balance_protocol: 'gs_cpa',
    balance_lines: 10, balance_start_output: false,
  });
  ports: PortInfo[] = $state([]);
  job: ScanJob | null = $state(null);
  connected = $state(false);
  logPath = $state('');
  message = $state('正在连接本地业务服务…');
  error = $state('');
  autoReview = $state(false);
  autoInterval = $state(30);
  submitting = $state(false);
  cancelling = $state(false);
  active = $derived(this.submitting || this.job?.status === 'queued' || this.job?.status === 'running');
  private pollTimer?: ReturnType<typeof setTimeout>;
  private reviewTimer?: ReturnType<typeof setTimeout>;
  private heartbeatTimer?: ReturnType<typeof setInterval>;
  private disposed = false;
  private initialized = false;

  async connect() {
    try {
      const response = await api.health();
      if (this.disposed) return;
      const reconnected = !this.connected;
      this.connected = response.ok;
      this.logPath = response.log_path;
      if (this.connected && !this.initialized) {
        this.initialized = true;
        await this.load();
        await this.refreshPorts();
      } else if (this.connected && reconnected) {
        this.message = this.active ? '连接已恢复，正在获取扫描状态…' : '本地业务服务连接已恢复。';
      }
    } catch (error) {
      if (!this.disposed) {
        this.connected = false;
        this.message = '业务服务未连接，正在重试…';
      }
    }
  }

  async initialize() {
    await this.connect();
    if (this.disposed) return;
    this.heartbeatTimer = setInterval(() => void this.connect(), 3000);
  }

  dispose() {
    this.disposed = true;
    clearTimeout(this.pollTimer);
    clearTimeout(this.reviewTimer);
    clearInterval(this.heartbeatTimer);
  }

  selectDevice(device: DeviceType) {
    this.config.device = device;
    if (device === 'azbil') {
      this.config.parity = 'E';
      this.config.stopbits = 1;
      this.config.start_address = Math.min(this.config.start_address, 127);
      this.config.end_address = Math.min(this.config.end_address, 127);
    } else if (device === 'balance') {
      this.config.baudrate = 1200;
      this.config.parity = 'N';
      this.config.stopbits = 1;
    }
  }

  async refreshPorts() {
    try {
      this.ports = await api.ports();
      this.message = `发现 ${this.ports.length} 个系统串口，请按协议扫描确认在线状态。`;
    } catch (error) { this.fail(error); }
  }

  async load() {
    try {
      const config = await api.loadProfile();
      if (config) this.config = config;
      this.message = config ? '已加载上次保存的配置。' : '请选择串口后开始扫描。';
    } catch (error) { this.fail(error); }
  }

  async save() {
    try {
      this.config = await api.saveProfile(this.snapshot());
      this.error = '';
      this.message = '配置已保存。';
    } catch (error) { this.fail(error); }
  }

  async importProfile(file: File) {
    try {
      this.config = await api.saveProfile(JSON.parse(await file.text()));
      this.error = '';
      this.message = '配置已导入并保存。';
    } catch (error) { this.fail(error); }
  }

  async downloadProfile() {
    try {
      const config = await api.saveProfile(this.snapshot());
      this.download('rka-profile.json', JSON.stringify(config, null, 2), 'application/json');
    } catch (error) { this.fail(error); }
  }

  async downloadLog() {
    try {
      const { filename, content } = await api.exportLog();
      this.download(filename, content, 'text/plain');
    } catch (error) { this.fail(error); }
  }

  async beginScan() {
    if (this.active || this.disposed) return;
    this.submitting = true;
    this.error = '';
    this.cancelling = false;
    clearTimeout(this.reviewTimer);
    try {
      this.job = await api.startScan(this.snapshot());
      this.message = '扫描中；停止将在当前串口读操作结束后生效。';
      this.schedulePoll();
    } catch (error) {
      this.autoReview = false;
      this.fail(error);
    } finally { this.submitting = false; }
  }

  async stopScan() {
    this.autoReview = false;
    clearTimeout(this.reviewTimer);
    if (!this.job || !this.active || this.cancelling) return;
    this.cancelling = true;
    try {
      this.job = await api.cancelScan(this.job.id);
      this.message = '正在停止；等待当前串口操作结束并清理连接…';
    } catch (error) { this.cancelling = false; this.fail(error); }
  }

  cancelReview(enabled: boolean) {
    this.autoReview = enabled;
    if (!enabled) clearTimeout(this.reviewTimer);
  }

  private snapshot(): ScanConfig {
    return JSON.parse(JSON.stringify(this.config)) as ScanConfig;
  }

  private schedulePoll() {
    clearTimeout(this.pollTimer);
    if (this.disposed) return;
    this.pollTimer = setTimeout(() => void this.poll(), 350);
  }

  private async poll() {
    if (!this.job || this.disposed) return;
    try {
      this.job = await api.getScan(this.job.id);
      if (this.job.status === 'queued' || this.job.status === 'running') {
        this.schedulePoll();
        return;
      }
      this.cancelling = false;
      const status = this.job.status;
      this.message = status === 'cancelled' ? '扫描已停止，结果不完整。'
        : status === 'error' ? `扫描异常：${this.job.error}` : '扫描完成。';
      if (this.autoReview && status === 'completed') {
        const delay = Math.min(300, Math.max(5, Number(this.autoInterval) || 30));
        this.message = `扫描完成，${delay} 秒后复查。`;
        this.reviewTimer = setTimeout(() => {
          if (this.autoReview && !this.disposed) void this.beginScan();
        }, delay * 1000);
      }
    } catch (error) {
      this.fail(error);
      this.schedulePoll(); // Keep ownership of the active job across a transient transport failure.
    }
  }

  private fail(error: unknown) {
    this.error = error instanceof Error ? error.message : String(error);
  }

  private download(name: string, text: string, type: string) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
